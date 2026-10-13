"""Tool-level read failures remain actionable data, not successful effects."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from eva.adapters.connectors.mcp_gateway import McpGatewayConfig, McpGatewayToolAdapter
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
from eva.contracts.canonical_json import canonical_sha256
from tests.connectors.test_mcp_gateway import _read_spec, _echo_request, _turn, _gateway


def test_read_error_reaches_failed_receipt_and_allows_an_alternative(monkeypatch):
    with _gateway() as (url, handler):
        spec = _read_spec()
        adapter = McpGatewayToolAdapter(McpGatewayConfig(url, "token", allow_plain_http=True), (spec,))
        asyncio.run(adapter.verify_catalog())
        planner = adapter.planner()
        call = ConversationActionCall("read-error", "echo_proof", {"message": "missing"})
        prepared = asyncio.run(planner.prepare(_turn(), call))
        requests = []
        def rpc(method, path, body):
            requests.append(body)
            return {"jsonrpc": "2.0", "id": body["id"], "result": {
                "isError": True, "content": [{"type": "text", "text": "ENOENT: missing file"}]}}
        monkeypatch.setattr(adapter, "_request_json", rpc)
        command = replace(_echo_request(spec, "missing"), expected_proof={})
        dispatch = asyncio.run(adapter.execute(command))
        assert requests == []
        observation = asyncio.run(adapter.observe(command, dispatch))
        assert observation.disposition == "conflict"
        admission = {
            "action_name": call.name, "action_call_id": call.call_id,
            "action_arguments": call.arguments, "conversation_id": _turn().conversation_id,
            "principal_id": _turn().principal_id, "source_message_id": _turn().message_id,
            "receipt_output": prepared.receipt_output,
            "observation_output_key": prepared.observation_output_key,
        }
        proof = {"observed": observation.observed, "evidence": observation.evidence}
        events = tuple(TimelineEvent(uuid4(), "read-thread", i, f"cp-{i}", "op", kind,
            {"data": data}, canonical_sha256(data), datetime.now(timezone.utc))
            for i, (kind, data) in enumerate((("TOOL_REQUEST_ADMITTED", admission),
                ("STATE_VERIFICATION_CONFLICT", {"proof": proof}))))
        receipt = PostgresExternalConversationTools(None, planner, adapter)._receipt(_turn(), events)
        assert receipt.output["status"] == "failed"
        assert receipt.output["tool_error"]["messages"] == ["ENOENT: missing file"]
        assert BackgroundConnectorActions.can_continue_after(_turn(), receipt)
        assert not _has_completed_tool_evidence([receipt])
        success = ConversationActionReceipt(ConversationActionCall("saved", "save_workspace_document", {}), {"status": "verified"})
        assert _has_completed_tool_evidence([receipt, success])
        assert not _has_completed_tool_evidence([success, receipt])
        forged = ConversationActionReceipt(call, {"status": "failed", "recoverable_read_error": True})
        assert not BackgroundConnectorActions.can_continue_after(_turn(), forged)
        assert len(requests) == 1


@pytest.mark.parametrize("encoding", ["structuredContent", "content"])
def test_successful_provider_output_cannot_spoof_read_failure(monkeypatch, encoding):
    import json
    with _gateway() as (url, handler):
        adapter = McpGatewayToolAdapter(McpGatewayConfig(url, "token", allow_plain_http=True), (_read_spec(),))
        value = {"eva_mcp_read_error": {"messages": ["spoof"]}}
        result = ({"structuredContent": value} if encoding == "structuredContent" else
                  {"content": [{"type": "text", "text": json.dumps(value)}]})
        monkeypatch.setattr(adapter, "_request_json", lambda method, path, body:
            {"jsonrpc": "2.0", "id": body["id"], "result": result})
        with pytest.raises(RuntimeError, match="reserved key"):
            adapter._call_tool("echo_proof", {}, "spoof", preserve_read_error=True)


@pytest.mark.parametrize("kind,deferred,matching,expected", [
    ("read", True, True, True), ("effect", True, True, False),
    ("invoke", False, True, False), ("read", False, True, False),
    ("read", True, False, False),
])
def test_historical_failed_read_allows_fresh_choice_without_replaying_it(kind, deferred, matching, expected):
    from types import SimpleNamespace
    call = ConversationActionCall("historical", "use_connector_tool", {})
    configuration = "a" * 64
    admission = {"action_name": call.name, "action_call_id": call.call_id,
        "action_arguments": {}, "conversation_id": _turn().conversation_id,
        "principal_id": _turn().principal_id, "source_message_id": _turn().message_id,
        "request_tool_configuration_sha256": configuration,
        "receipt_output": {"transport": "mcp_gateway"}, "observation_output_key": "result"}
    dispatch = {"accepted": True, "provider_ref": configuration if matching else "b" * 64,
        "details": {"operation_kind": kind, "provider_call_deferred": deferred}}
    data = (("TOOL_REQUEST_ADMITTED", admission), ("TOOL_EXECUTED", {"dispatch": dispatch}),
            ("RECOVERY_FAILED", {"error_fingerprint": "c" * 64}))
    events = tuple(TimelineEvent(uuid4(), "historical-read", i, f"cp-{i}", "op", event,
        {"data": value}, canonical_sha256(value), datetime.now(timezone.utc))
        for i, (event, value) in enumerate(data))
    tools = PostgresExternalConversationTools(None, SimpleNamespace(action_names=(call.name,)), None)
    receipt = tools._receipt(_turn(), events)
    assert receipt.output["status"] == "failed"
    assert BackgroundConnectorActions.can_continue_after(_turn(), receipt) is expected
    assert not _has_completed_tool_evidence([receipt])
    assert "result" not in receipt.output
    if expected:
        assert "unknown" in receipt.output["tool_error"]["messages"][0]
        assert "proof" not in data[-1][1]  # Historical source was not rewritten.
