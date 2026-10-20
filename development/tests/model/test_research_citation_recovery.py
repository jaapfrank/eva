"""Reject bad source URLs without losing a completed provider measurement."""
import pytest
from eva.adapters.model.contracts import ConversationModelError, ResearchCitationError
from tests.model.test_hosted_research_usage import run, terminal, search

URL = "https://example.org/code#L1"


def message(url=URL, title="Code"):
    return {"type": "message", "content": [{"type": "output_text", "text": "Finding.",
        "annotations": [{"type": "url_citation", "url": url, "title": title}]}]}


@pytest.mark.parametrize("title,expected", [("   ", "example.org"), (None, "example.org"),
    ("x" * 501, "x" * 500), ("  Code  ", "Code")])
def test_title_metadata_does_not_discard_the_exact_valid_source(monkeypatch, tmp_path, title, expected):
    parsed, usage = run(monkeypatch, tmp_path, [terminal([message(title=title)])])
    assert parsed[0] == "Finding." and len(parsed[1]) == 1
    assert parsed[1][0].url == URL and parsed[1][0].title == expected
    assert usage.measurement == "measured"


@pytest.mark.parametrize("url", ["file:///private", "http://example.org/code", "https://example.org/" + "x" * 2048])
def test_bad_url_stays_invalid_but_terminal_usage_is_not_lost(monkeypatch, tmp_path, url):
    with pytest.raises(ResearchCitationError) as caught:
        run(monkeypatch, tmp_path, [terminal([search(), message(url=url)])])
    used = caught.value.model_usage
    assert used.measurement == "measured"
    assert (used.model_attempts, used.input_tokens, used.output_tokens, used.tool_calls) == (1, 20, 5, 1)


@pytest.mark.parametrize("ending", ["missing", "contradictory", "failed"])
def test_no_measured_usage_from_a_missing_or_contradictory_completion(monkeypatch, tmp_path, ending):
    from tests.model.test_hosted_research_usage import done
    events = ([done(message(url="file:///private"))] if ending == "missing" else
        [terminal([message()]), terminal()] if ending == "contradictory" else
        [terminal(status="incomplete")])
    with pytest.raises(ConversationModelError) as caught:
        run(monkeypatch, tmp_path, events)
    assert caught.value.model_usage is None
