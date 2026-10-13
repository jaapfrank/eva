"""A reported tool failure survives the durable receipt without authorizing retry."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.conversation import ConversationActionCall
from eva.application.orchestration import ToolExecutionRejected, VerifiedToolOrchestrator
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
from eva.contracts.canonical_json import canonical_sha256
from tests.application.test_orchestration import Timeline
from tests.connectors.test_mcp_gateway import _adapter, _echo_request, _gateway, _spec, _turn


def test_rejected_rpc_error_survives_durable_receipt_without_replay(monkeypatch):
    with _gateway() as (url, _):
        adapter = _adapter(url)
        asyncio.run(adapter.verify_catalog())
        call = ConversationActionCall("diagnostic", "echo_proof", {"message": "sample"})
        planner = adapter.planner()
        prepared = asyncio.run(planner.prepare(_turn(), call))
        requests = []

        def rpc(method, path, body):
            requests.append(body)
            return {"jsonrpc": "2.0", "id": body["id"], "result": {
                "isError": True, "content": [{"type": "text", "text": "Resource not available"},
                    {"type": "text", "text": "x" * 6000},
                    {"type": "text", "text": "not delivered"}]}}

        monkeypatch.setattr(adapter, "_request_json", rpc)
        timeline = Timeline()
        command = replace(_echo_request(_spec(), "sample"), thread_id=timeline.state.thread_id)
        result = asyncio.run(VerifiedToolOrchestrator(timeline, adapter).run(command))
        assert result.status == "rejected"
        rejection = timeline.recorded[-1].payload["data"]
        assert rejection["reason"] == "MCP tool rejected the operation"
        assert rejection["diagnostic"]["messages"] == ["Resource not available", "x" * 4096]
        admission = {
            "action_name": call.name, "action_call_id": call.call_id,
            "action_arguments": call.arguments, "conversation_id": _turn().conversation_id,
            "principal_id": _turn().principal_id, "source_message_id": _turn().message_id,
            "receipt_output": prepared.receipt_output,
            "observation_output_key": prepared.observation_output_key,
        }
        data = (("TOOL_REQUEST_ADMITTED", admission), ("TOOL_REJECTED", rejection))
        events = tuple(TimelineEvent(uuid4(), "diagnostic-thread", i, f"cp-{i}", "op", kind,
            {"data": value}, canonical_sha256(value), datetime.now(timezone.utc))
            for i, (kind, value) in enumerate(data))
        tools = PostgresExternalConversationTools(None, planner, adapter)
        receipt = tools._receipt(_turn(), events)
        assert receipt.output["status"] == "rejected"
        assert receipt.output["tool_error"]["untrusted"] is True
        assert receipt.output["tool_error"]["effect_outcome"] == "unknown"
        assert receipt.output["proof"] is None
        assert not BackgroundConnectorActions.can_continue_after(_turn(), receipt)
        assert not _has_completed_tool_evidence([receipt])
        assert tools._receipt(_turn(), events) == receipt
        assert len(requests) == 1

        # Previously stored rejections retain their historical lack of detail.
        old = replace(events[-1], payload={"data": {"reason": rejection["reason"]}})
        assert "tool_error" not in tools._receipt(_turn(), (*events[:-1], old)).output


@pytest.mark.parametrize("content", [None, [], [{"type": "image", "data": "ignored"}]])
def test_non_text_tool_error_has_explicit_unknown_diagnostic(monkeypatch, content):
    with _gateway() as (url, _):
        adapter = _adapter(url)
        monkeypatch.setattr(adapter, "_request_json", lambda method, path, body:
            {"jsonrpc": "2.0", "id": body["id"], "result": {"isError": True, "content": content}})
        with pytest.raises(ToolExecutionRejected) as rejected:
            adapter._call_tool("proof-echo", {}, "diagnostic")
        assert rejected.value.diagnostic["messages"] == [
            "MCP tool reported an error without a text message."]
        assert rejected.value.diagnostic["effect_outcome"] == "unknown"
