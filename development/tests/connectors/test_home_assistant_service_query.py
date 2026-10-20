"""One durable invocation resolves live service/target without model round trips."""
import asyncio
from contextlib import contextmanager
import copy
import json

import pytest

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.adapters.postgres.connector_results import ConnectorResults, result_capture
from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
from eva.application.conversation import ConversationConflict, ConversationActionCall, ConversationActionInputRejected
from eva.application.orchestration import ToolRequest
from tests.connectors.test_connector_results import Timeline
from tests.connectors.test_mcp_discovery import TURN


def arguments(**changes):
    return {"transport": "rest", "method": "POST", "path": "/api/services/sample/get_values?return_response",
        "body_json": '{"mode":"daily"}', "target_query": "*", "response_pointer": "",
        "offset": 0, "limit": 50, **changes}


def fixture(monkeypatch, *, definitions=None, states=None, after_states=lambda: None, response_error=None):
    definition = {"target": {"entity": [{"domain": ["sample"], "supported_features": [1, 2]}]},
        "response": {"optional": False}, "fields": {"mode": {"required": True,
            "selector": {"select": {"options": ["daily", "hourly"], "multiple": False, "custom_value": False}}}}}
    definitions = definitions if definitions is not None else {"sample": {"get_values": definition}}
    states = states if states is not None else [{"entity_id": "sample.actual", "state": "ready",
        "attributes": {"friendly_name": "Actual source", "supported_features": 1, "private_unrelated": "Never project this"}}]
    reads, posts = [], []
    class Socket:
        def send(self, text):self.command = json.loads(text);reads.append(self.command["type"])
        def recv(self, **kwargs):
            value = definitions if self.command["type"] == "get_services" else states
            if self.command["type"] == "get_states":after_states()
            return json.dumps({"id": self.command["id"], "type": "result", "success": True, "result": value})
    @contextmanager
    def session():yield Socket()
    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        def iter_bytes(self):
            if response_error is not None:raise response_error
            yield b'{"service_response":{"sample.actual":{"values":[1,2,3]}}}'
    class Client:
        def __init__(self, **kwargs):assert kwargs["follow_redirects"] is False and 0 < kwargs["timeout"] <= 5
        def __enter__(self):return self
        def __exit__(self, *args):pass
        @contextmanager
        def stream(self, method, url, **kwargs):posts.append((method, url, kwargs["json"]));yield Response()
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_api.httpx.Client", Client)
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="test-secret")
    tool.admin._session = session
    return tool, reads, posts, definition


def request(args=None):
    return ToolRequest("thread", "query-operation", "checkpoint", "home_assistant.api.request", args or arguments(), {})


def test_resolves_live_target_once_and_preserves_source_without_registry(monkeypatch):
    tool, reads, posts, _ = fixture(monkeypatch)
    actual = request();timeline = Timeline();capture = ConnectorResults(timeline, TURN, lambda _: None)
    prepared = asyncio.run(tool.prepare(TURN, ConversationActionCall("call", "home_assistant_request", actual.arguments)))
    assert reads == posts == []  # preparation has no network/effect
    with result_capture(capture):
        dispatch = asyncio.run(tool.execute(actual))
        result = asyncio.run(tool.observe(actual, dispatch)).evidence["observation"]
        assert asyncio.run(tool.execute(actual)) == dispatch
        assert asyncio.run(tool.observe(actual, None)).disposition == "pending"
    assert reads == ["get_services", "get_states"] and len(posts) == 1
    assert posts[0] == ("POST", "http://ha.local/api/services/sample/get_values?return_response",
        {"mode": "daily", "entity_id": "sample.actual"})
    assert result["service_request_sent"] is True and result["external_state_verified"] is False
    assert result["discovery"]["entity_id"] == "sample.actual"
    assert result["discovery"]["name"] == "Actual source"
    assert "Never project this" not in json.dumps(result)
    assert len(result["discovery"]["entity_catalog_sha256"]) == 64
    assert prepared.execution_timeout_seconds == 25


@pytest.mark.parametrize("change,code", [
    ("ambiguous", "target_absent_or_ambiguous"), ("absent", "target_absent_or_ambiguous"),
    ("unavailable", "target_unavailable"), ("no_response", "mandatory_response_service_unavailable"),
    ("unknown_selector", "unsupported_service_target"), ("bad_option", "service_field_value_invalid"),
    ("missing_field", "required_service_field_missing"), ("unknown_field", "service_fields_differ"),
])
def test_discovery_rejections_never_invoke_service(monkeypatch, change, code):
    tool, _, _, definition = fixture(monkeypatch)
    states = [{"entity_id": "sample.actual", "state": "ready", "attributes": {"supported_features": 1}}]
    args = arguments();definitions = {"sample": {"get_values": copy.deepcopy(definition)}}
    if change == "ambiguous":states.append({**states[0], "entity_id": "sample.second"})
    if change == "absent":states = []
    if change == "unavailable":states[0]["state"] = "unavailable"
    if change == "no_response":definitions["sample"]["get_values"]["response"]["optional"] = True
    if change == "unknown_selector":definitions["sample"]["get_values"]["target"]["entity"][0]["future_filter"] = True
    if change == "bad_option":args["body_json"] = '{"mode":"invented"}'
    if change == "missing_field":args["body_json"] = '{}'
    if change == "unknown_field":args["body_json"] = '{"mode":"daily","invented":true}'
    tool, _, posts, _ = fixture(monkeypatch, definitions=definitions, states=states)
    result = asyncio.run(tool.execute(request(args))).details["response"]
    assert result["success"] is False and result["service_request_sent"] is False
    assert result["error_code"] == code and not posts


@pytest.mark.parametrize("changes", [
    {"body_json": '{"entity_id":"sample.injected","mode":"daily"}'},
    {"path": "/api/services/sample/get_values"}, {"method": "GET"},
    {"target_query": " "}, {"target_query": 2},
])
def test_invalid_composite_shape_rejected_before_network(monkeypatch, changes):
    tool, reads, posts, _ = fixture(monkeypatch)
    with pytest.raises(ConversationConflict):asyncio.run(tool.execute(request(arguments(**changes))))
    assert not reads and not posts


def test_exact_actual_name_resolves_among_other_targets(monkeypatch):
    states = [{"entity_id": "sample.first", "state": "ready", "attributes": {"friendly_name": "First", "supported_features": 1}},
        {"entity_id": "sample.second", "state": "ready", "attributes": {"friendly_name": "Second", "supported_features": 1}}]
    tool, _, posts, _ = fixture(monkeypatch, states=states)
    asyncio.run(tool.execute(request(arguments(target_query="Second"))))
    assert posts[0][2]["entity_id"] == "sample.second"


@pytest.mark.parametrize("kind", ["stop", "revoked"])
def test_stop_or_revocation_after_discovery_prevents_post(monkeypatch, kind):
    signal = CancellationSignal();revoked = []
    def after():
        if kind == "stop":signal.cancel()
        else:revoked.append(True)
    def authorize(_):
        if revoked:raise ConversationConflict("revoked")
    tool, reads, posts, _ = fixture(monkeypatch, after_states=after)
    capture = ConnectorResults(Timeline(), TURN, authorize)
    with result_capture(capture), cancellation_scope(signal):
        with pytest.raises((WorkInterrupted, ConversationConflict)):
            asyncio.run(tool.execute(request()))
    assert reads == ["get_services", "get_states"] and not posts


def test_uncertain_service_response_is_not_replayed(monkeypatch):
    tool, reads, posts, _ = fixture(monkeypatch, response_error=TimeoutError("response lost"))
    capture = ConnectorResults(Timeline(), TURN, lambda _: None)
    with result_capture(capture):
        with pytest.raises(TimeoutError):asyncio.run(tool.execute(request()))
        with pytest.raises(ConversationConflict, match="uncertain"):asyncio.run(tool.execute(request()))
    assert len(posts) == 1 and reads == ["get_services", "get_states"]


def test_discovery_consuming_deadline_cannot_start_post(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_service_query.time.monotonic", lambda: clock[0])
    tool, _, posts, _ = fixture(monkeypatch, after_states=lambda: clock.__setitem__(0, 130.0))
    with pytest.raises(TimeoutError):asyncio.run(tool.execute(request()))
    assert not posts


def test_malformed_body_is_a_local_negative_receipt(monkeypatch):
    tool, reads, posts, _ = fixture(monkeypatch)
    result = asyncio.run(tool.execute(request(arguments(body_json='{broken')))).details["response"]
    assert result["request_sent"] is False and result["error_code"] == "invalid_body_json"
    assert not reads and not posts


@pytest.mark.parametrize("key", ["target", "area_id", "device_id", "floor_id", "label_id"])
def test_caller_cannot_expand_the_resolved_target(monkeypatch, key):
    tool, reads, posts, _ = fixture(monkeypatch)
    with pytest.raises(ConversationConflict):
        asyncio.run(tool.execute(request(arguments(body_json=json.dumps({"mode": "daily", key: "all"})))))
    assert not reads and not posts


@pytest.mark.parametrize("body", [{"type": "get_services", "id": 1}, [], {"type": []}, {"type": ""}, {}])
def test_invalid_websocket_envelope_returns_feedback_without_transport(monkeypatch, body):
    tool, reads, posts, _ = fixture(monkeypatch)
    args = arguments(transport="websocket", method="GET", path="", target_query="", body_json=json.dumps(body))
    prepared = asyncio.run(tool.prepare(TURN, ConversationActionCall("bad", "home_assistant_request", args)))
    result = asyncio.run(tool.execute(request(prepared.arguments))).details["response"]
    assert result["request_sent"] is False and result["error_code"] == "invalid_websocket_command"
    assert not reads and not posts


def test_conflicting_composite_input_uses_existing_bounded_core_repair(monkeypatch):
    tool, reads, posts, _ = fixture(monkeypatch)
    args = arguments(body_json='{"mode":"daily","entity_id":"sample.actual"}')
    with pytest.raises(ConversationActionInputRejected) as error:
        asyncio.run(tool.prepare(TURN, ConversationActionCall("bad", "home_assistant_request", args)))
    assert error.value.fields == ("target_query", "body_json")
    assert not reads and not posts
