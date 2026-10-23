"""Transported camera image is not proof of successful local interpretation."""
from contextlib import contextmanager
from types import SimpleNamespace
import asyncio
import hashlib
import json
import pytest
import httpx2 as httpx
from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.application.conversation import ConversationStopped
from tests.connectors.test_home_assistant_api import args


@pytest.mark.parametrize("error,code", [(httpx.ConnectError("private endpoint and image"), "local_vision_unreachable"),
    (httpx.ReadTimeout("private request"), "local_vision_timeout"), (None, None)])
def test_received_image_and_analysis_are_reported_separately(monkeypatch, error, code):
    raw = b"\xff\xd8\xffsynthetic-image"
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def stream(self, *args, **kwargs):
            calls.append(args)
            yield SimpleNamespace(status_code=200, headers={"content-type": "image/jpeg"}, iter_bytes=lambda: iter([raw]))
    monkeypatch.setattr(httpx, "Client", Client)
    class Vision:
        def analyze(self, snapshot):
            assert snapshot.data == raw
            if error is not None: raise error
            return SimpleNamespace(payload={"description": "Synthetic scene", "image_sha256": snapshot.sha256})
    tool = HomeAssistantApiTool(base_url="http://ha.invalid", token="private-token", vision=Vision())
    request = SimpleNamespace(arguments=args(path="/api/camera_proxy/camera.synthetic"))
    dispatch = asyncio.run(tool.execute(request))
    observation = asyncio.run(tool.observe(request, dispatch))
    result = observation.evidence["observation"]
    body = json.loads(result["response_json"])
    assert observation.disposition == "verified" and len(calls) == 1
    assert result["http_status"] == 200 and result["success"] is (error is None)
    assert result["external_state_verified"] is False
    if error is not None:
        assert body["error"] == code and body["image_received"] is True
        assert body["image_sha256"] == hashlib.sha256(raw).hexdigest()
        assert body["analysis_available"] is False and "description" not in body
        assert "private" not in json.dumps(body) and "synthetic-image" not in json.dumps(body)
    else: assert body["description"] == "Synthetic scene"


def test_analyzer_authority_or_programming_failure_is_not_swallowed(monkeypatch):
    raw = b"\xff\xd8\xffsynthetic-image"
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def stream(self, *args, **kwargs):
            yield SimpleNamespace(status_code=200, headers={"content-type": "image/jpeg"}, iter_bytes=lambda: iter([raw]))
    monkeypatch.setattr(httpx, "Client", Client)
    class Vision:
        def analyze(self, snapshot): raise ConversationStopped("stop")
    tool = HomeAssistantApiTool(base_url="http://ha.invalid", token="private-token", vision=Vision())
    with pytest.raises(ConversationStopped): tool._request(args(path="/api/camera_proxy/camera.synthetic"))
