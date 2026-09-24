"""Protocol tests for general HA requests; no real home effects or model calls."""
import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.application.conversation import ConversationConflict


def args(**changes):
    return {"transport": "rest", "method": "GET", "path": "/api/services",
        "body_json": "", "response_pointer": "", "offset": 0, "limit": 50, **changes}


def test_arbitrary_service_and_full_automation_config_use_same_transport(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def iter_bytes(self):
            yield json.dumps({"saved": True}).encode()
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            yield Response()
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_api.httpx.Client", Client)
    tool = HomeAssistantApiTool(base_url="http://ha.local:8123", token="local-fixture")
    for path, body in [
        ("/api/services/climate/set_temperature", {"entity_id": "climate.room", "temperature": 21}),
        ("/api/config/automation/config/existing", {"alias": "Existing", "triggers": [{"trigger": "event", "event_type": "custom"}],
            "actions": [{"action": "fan.set_percentage", "data": {"percentage": 40}}]})]:
        request = SimpleNamespace(arguments=args(method="POST", path=path, body_json=json.dumps(body)))
        dispatch = asyncio.run(tool.execute(request))
        receipt = asyncio.run(tool.observe(request, dispatch))
        assert receipt.disposition == "verified"
        assert receipt.evidence["observation"]["external_state_verified"] is False
        assert calls[-1][0:2] == ("POST", "http://ha.local:8123" + path)
        assert calls[-1][2]["json"] == body
        assert asyncio.run(tool.observe(request, None)).disposition == "pending"
    assert len(calls) == 2  # observe never replays the request


def test_discovery_websocket_and_response_projection():
    sent = []
    class Socket:
        def send(self, value): sent.append(json.loads(value))
        def recv(self, **kwargs):
            return json.dumps({"id": 1, "type": "result", "success": True,
                "result": {"new_domain": {"new_service": {"fields": {"level": {}}}}}})
    @contextmanager
    def session(): yield Socket()
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    tool.admin._session = session
    result = tool._request(args(transport="websocket", path="", body_json='{"type":"get_services"}', response_pointer="/new_domain"))
    assert sent == [{"type": "get_services", "id": 1}]
    assert result["response"] == {"new_service": {"fields": {"level": {}}}}
    assert result["success"] is True


@pytest.mark.parametrize("path", ["https://elsewhere/api/", "//elsewhere/api/", "/api/../auth", "/api/%2e%2e/auth", "/api/%252e%252e/auth", "/api/ok#fragment"])
def test_configured_destination_cannot_be_changed(path):
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    with pytest.raises(ConversationConflict): tool._arguments(args(path=path))


def test_rejected_api_response_is_not_successful_work():
    from eva.application.orchestration import ToolDispatchResult
    from eva.contracts.canonical_json import canonical_sha256
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    request = SimpleNamespace(arguments=args())
    result = {"success": False, "http_status": 400, "response": {"message": "Invalid service"}}
    dispatch = ToolDispatchResult(True, canonical_sha256(result), {"response": result, "request_sha256": canonical_sha256(request.arguments)})
    observation = asyncio.run(tool.observe(request, dispatch))
    assert observation.disposition == "conflict"
    assert observation.evidence["observation"]["http_status"] == 400
