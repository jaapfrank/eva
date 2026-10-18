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
            yield json.dumps({"saved": True, "temperature": 21.5}).encode()
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
        assert json.loads(receipt.evidence["observation"]["response_json"])["temperature"] == 21.5
        assert receipt.evidence["observation"]["external_state_verified"] is False
        assert "source_response_json" not in receipt.evidence["observation"]
        assert "source_response_manifest" not in receipt.evidence["observation"]
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
    assert json.loads(result["response_json"]) == {"new_service": {"fields": {"level": {}}}}
    assert result["success"] is True


@pytest.mark.parametrize("path", ["https://elsewhere/api/", "//elsewhere/api/", "/api/../auth", "/api/%2e%2e/auth", "/api/%252e%252e/auth", "/api/ok#fragment"])
def test_configured_destination_cannot_be_changed(path):
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    with pytest.raises(ConversationConflict): tool._arguments(args(path=path))


def test_rejected_api_response_remains_available_for_model_correction():
    from eva.application.orchestration import ToolDispatchResult
    from eva.contracts.canonical_json import canonical_sha256
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    request = SimpleNamespace(arguments=args())
    result = {"success": False, "http_status": 400, "response": {"message": "Invalid service"}}
    dispatch = ToolDispatchResult(True, canonical_sha256(result), {"response": result, "request_sha256": canonical_sha256(request.arguments)})
    observation = asyncio.run(tool.observe(request, dispatch))
    assert observation.disposition == "verified"
    assert observation.evidence["observation"]["http_status"] == 400


@pytest.mark.parametrize("outcomes, expected", [
    ([False], False), ([True, False], False), ([False, True], True),
    ([True], True),
])
def test_background_completion_requires_recovery_after_api_failure(outcomes, expected):
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
    receipts = [SimpleNamespace(call=SimpleNamespace(name="home_assistant_request", arguments={}),
        output={"status": "verified", "result": {"success": success}})
        for success in outcomes]
    assert _has_completed_tool_evidence(receipts) is expected


@pytest.mark.parametrize("result", [None, {}, {"success": "false"}])
def test_background_completion_rejects_missing_or_malformed_api_result(result):
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
    receipt = SimpleNamespace(call=SimpleNamespace(name="home_assistant_request", arguments={}),
        output={"status": "verified", "result": result})
    assert not _has_completed_tool_evidence([receipt])


@pytest.mark.parametrize("transport,path", [("rest", "/api/template"), ("websocket", "")])
def test_malformed_json_is_a_local_failed_receipt_without_transport(monkeypatch, transport, path):
    from eva.application.conversation import ConversationActionCall
    def no_transport(*args, **kwargs):
        pytest.fail("invalid JSON must never open HA transport")
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_api.httpx.Client", no_transport)
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    tool.admin._session = no_transport
    call = ConversationActionCall("bad-json", "home_assistant_request",
        args(transport=transport, path=path, method="POST", body_json='{"template":'))
    prepared = asyncio.run(tool.prepare(None, call))
    request = SimpleNamespace(arguments=prepared.arguments)
    dispatch = asyncio.run(tool.execute(request))
    proof = asyncio.run(tool.observe(request, dispatch))
    result = proof.evidence["observation"]
    assert result["success"] is False and result["request_sent"] is False
    assert result["error_code"] == "invalid_body_json"
    assert proof.evidence["source"] == "home_assistant_request_validation"
    assert "http_status" not in result and "response_json" not in result
    assert proof.evidence["external_state_verified"] is False


@pytest.mark.parametrize("changes", [
    {"path": "https://elsewhere/api/", "body_json": "{"},
    {"transport": "websocket", "path": "/api/", "body_json": "{"},
    {"transport": "websocket", "path": "", "body_json": '{"type":"auth"}'},
    {"transport": "websocket", "path": "", "body_json": '{"type":"get_services","access_token":"not-allowed"}'},
])
def test_request_boundary_failures_are_not_correctable_json_receipts(changes):
    from eva.application.conversation import ConversationActionCall
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    call = ConversationActionCall("bad-boundary", "home_assistant_request", args(**changes))
    with pytest.raises(ConversationConflict):
        asyncio.run(tool.prepare(None, call))
    with pytest.raises(ConversationConflict):
        asyncio.run(tool.execute(SimpleNamespace(arguments=call.arguments)))


def test_paginated_full_response_is_retained_once_and_read_locally():
    from eva.adapters.postgres.connector_results import ConnectorResults, result_capture, _encoded
    from eva.application.orchestration import ToolRequest
    from tests.connectors.test_connector_results import Timeline
    from tests.connectors.test_mcp_discovery import TURN

    rows = [{"index": index, "platform": "target" if index >= 2400 else "other",
             "measurement": index + 0.5} for index in range(2465)]
    sent = []
    class Socket:
        def send(self, value): sent.append(json.loads(value))
        def recv(self, **kwargs):
            return json.dumps({"id": 1, "type": "result", "success": True, "result": rows})
    @contextmanager
    def session(): yield Socket()
    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    tool.admin._session = session
    request = ToolRequest("thread", "api-operation", "checkpoint", "home_assistant.api.request",
        args(transport="websocket", path="", body_json='{"type":"config/entity_registry/list"}'), {})
    timeline = Timeline()
    capture = ConnectorResults(timeline, TURN, lambda _: None)
    with result_capture(capture):
        dispatch = asyncio.run(tool.execute(request))
        observation = asyncio.run(tool.observe(request, dispatch)).evidence["observation"]
        assert observation["success"] is True
        assert observation["total"] == 2465 and observation["next_offset"] == 50
        assert json.loads(observation["response_json"]) == rows[:50]
        assert observation["truncated"] is False
        assert observation["response_sha256"] == __import__("hashlib").sha256(
            json.dumps(rows, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        manifest = observation["source_response_manifest"]
        assert manifest["stored_complete"] and manifest["byte_length"] > 65536
        assert len(_encoded(observation)) < 65536
        assert asyncio.run(tool.execute(request)) == dispatch
    reader = manifest["reader"]["tool_id"]
    identifier = reader.split(":")[1]
    retained = json.loads(capture._bytes(identifier)[0])
    assert json.loads(retained["source_response_json"]) == rows
    assert retained["source_response_json"] == json.dumps(rows, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False)
    page = capture.read(reader, {"pointer": "/source_response_json", "offset": 0,
        "limit": 150, "query": '"index":2400'})
    assert page["found"] and '"platform":"target"' in page["text"]
    assert len(sent) == 1  # all original rows, local read and recovery; one API request


def test_full_response_above_capture_bound_is_not_claimed_or_reobserved():
    from eva.adapters.postgres.connector_results import ConnectorResults, result_capture
    from eva.application.orchestration import ToolRequest
    from tests.connectors.test_connector_results import Timeline
    from tests.connectors.test_mcp_discovery import TURN

    tool = HomeAssistantApiTool(base_url="http://ha.local", token="local-fixture")
    calls = []
    def response(arguments):
        calls.append(arguments)
        return {"success": True, "total": 100, "next_offset": 50,
                "response_json": "[]", "truncated": False,
                "source_response_json": json.dumps({"value": "x" * 2_097_152})}
    tool._request = response
    request = ToolRequest("thread", "oversize-operation", "checkpoint",
        "home_assistant.api.request", args(), {})
    capture = ConnectorResults(Timeline(), TURN, lambda _: None)
    with result_capture(capture):
        with pytest.raises(ConversationConflict, match="transport bound"):
            asyncio.run(tool.execute(request))
        with pytest.raises(ConversationConflict, match="uncertain"):
            asyncio.run(tool.execute(request))
    assert len(calls) == 1
    assert not any(event.event_type == "CONNECTOR_CAPTURE_SEALED"
                   for events in capture.timeline.rows.values() for event in events)


@pytest.mark.parametrize("historical_one_second, expected", [(True, "failed"), (False, "verified")])
def test_retained_read_verification_includes_native_authority_latency(monkeypatch, historical_one_second, expected):
    """Exercise the real stop/native wrapper without another HA request or model."""
    from datetime import UTC, datetime, timedelta
    from time import sleep
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.application.conversation import ConversationActionCall, ConversationTurn
    from eva.application.initiative_execution import initiative_execution
    from eva.application.memory_audience import MemoryAudience
    from eva.application.orchestration import ToolDispatchResult
    from eva.contracts.canonical_json import canonical_sha256

    now = datetime.now(UTC).replace(microsecond=0)
    turn = ConversationTurn(tenant_id="test-tenant", installation_id="test-installation",
        principal_id="test-owner", conversation_id="initiative-task:verification",
        message_id="initiative-execute:verification", text="Read current state",
        occurred_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), audience=MemoryAudience.private_person(principal_id="test-owner"))
    tool = HomeAssistantApiTool(base_url="http://unused.invalid", token="local-fixture")
    arguments = args(path="/api/states/binary_sensor.fixture", limit=50)
    prepared = asyncio.run(tool.prepare(turn, ConversationActionCall("read", "home_assistant_request", arguments)))
    # This is the already retained provider response; no execute/HTTP path is used.
    retained = {"http_status": 200, "success": True, "response_json": '{"state":"off"}',
        "external_state_verified": False}
    dispatch = ToolDispatchResult(True, canonical_sha256(retained),
        {"response": retained, "request_sha256": canonical_sha256(arguments)})
    def no_execute(*args, **kwargs):
        pytest.fail("verification must never resend the retained read")
    monkeypatch.setattr(tool, "execute", no_execute)
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_api.httpx.Client", no_execute)
    phases = []
    state = SimpleNamespace(thread_id="read-verification", revision=1, checkpoint_id="executed",
        operation_id="read", phase="tool_executed", status="waiting_for_proof", retry_count=0,
        last_error_fingerprint=None, deadline=now + timedelta(seconds=30))
    class Timeline:
        def transition(self, **values):
            phases.append(values["event_type"])
            return SimpleNamespace(**{**vars(state), **values, "revision": state.revision + 1})
        def record_recovery_failure(self, **values):
            assert values["error_text"] == "TimeoutError: "
            phases.append("RECOVERY_FAILED")
            return SimpleNamespace(**{**vars(state), "status": "failed"})
    class Guard:
        def check(self, actual): assert actual == turn
        def authorize_request(self, request, *, phase):
            assert phase == "observe"
            sleep(0.6)
    def authorize(actual):
        assert actual == turn
        sleep(0.6)
    from sqlalchemy.engine import make_url
    engine = SimpleNamespace(url=make_url("postgresql://"))
    route = PostgresExternalConversationTools(engine, tool, tool, authorize_turn=authorize)
    request = SimpleNamespace(thread_id=state.thread_id, checkpoint_prefix="read", operation_id="read",
        arguments=arguments, expected_proof={}, tool_configuration_sha256=None,
        verification_timeout_seconds=1 if historical_one_second else prepared.verification_timeout_seconds,
        poll_interval_milliseconds=100)
    with initiative_execution(Guard()):
        result = asyncio.run(route._orchestrator(turn, Timeline())._verify_or_resume(request, state, dispatch))
    assert result.status == expected
    assert phases == (["RECOVERY_FAILED"] if historical_one_second else ["STATE_VERIFIED"])
    if result.status == "verified":
        assert result.proof["evidence"]["observation"] == retained
