from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import timezone
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import McpGatewayConfig, McpGatewayToolAdapter, mcp_gateway_catalog_binding
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.conversation import ConversationActionCall
from eva.application.orchestration import ToolDispatchResult, VerifiedToolOrchestrator
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_discovery import McpDiscoveryToolAdapter, McpExecutionBinding
from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
from tests.application.test_orchestration import Event, NOW, Timeline
from tests.connectors.test_mcp_gateway import _catalog_row, _echo_request, _read_spec, _spec, _turn, _v1_spec
from tests.connectors.test_mcp_gateway_provider import _provider_catalog


CONFIG = McpGatewayConfig("https://unknown-provider.invalid", "private-token")
OUTPUT = {"text": "Uitgevoerd volgens de dienst", "items": ["eerste", "tweede"]}
ROW = _catalog_row(read_only=False)
DIGEST = canonical_sha256(mcp_gateway_catalog_binding(ROW, contract="EvaMcpGatewayProvider/v4"))


def _invoke_spec():
    return _read_spec(contract="EvaMcpGatewayProvider/v4", operation_kind="invoke",
                      execute_catalog_sha256=DIGEST, observe_catalog_sha256=DIGEST)


def _command(spec=None):
    return replace(_echo_request(spec or _invoke_spec(), "doe dit"),
                   thread_id="thread:1", expected_proof={}, verification_timeout_seconds=1)


def _provider(monkeypatch, output=OUTPUT):
    calls = []
    catalog_calls = []
    def request_json(self, method, path, payload):
        if method == "GET":
            catalog_calls.append(path)
            return {"tools": [ROW], "nextCursor": None}
        calls.append(payload)
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": output}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    return calls, catalog_calls


def test_invocation_persists_one_call_then_recovers_offline_and_projects_honest_receipt(monkeypatch):
    calls, catalog_calls = _provider(monkeypatch)
    spec = _invoke_spec()
    adapter = McpGatewayToolAdapter(CONFIG, (spec,))
    asyncio.run(adapter.verify_catalog())
    call = ConversationActionCall("invoke-1", spec.descriptor.name, {"message": "doe dit"})
    prepared = asyncio.run(adapter.planner().prepare(_turn(), call))
    assert prepared.observation_output_key == "result"
    assert prepared.expected_proof == {}
    assert prepared.receipt_output["external_state_verified"] is False
    assert "not independent" in adapter.conversation_tools((spec,))[0].completion_guidance
    timeline = Timeline()
    command = _command(spec)
    class CrashAfterDurableDispatch:
        execute = adapter.execute
        async def observe(self, request, dispatch):
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(VerifiedToolOrchestrator(timeline, CrashAfterDurableDispatch(), clock=lambda: NOW).run(command))
    assert timeline.state.phase == "tool_executed"
    assert len(calls) == 1
    # Emulate persisted JSON plus a fresh provider/cache after a process restart.
    timeline.recorded = [Event(e.event_type, json.loads(json.dumps(e.payload))) for e in timeline.recorded]
    def offline(*args, **kwargs):
        pytest.fail("invocation recovery must not contact the provider or current catalog")
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", offline)
    binding = McpExecutionBinding(spec, CONFIG, "new-connector", "registered-tool")
    class Registry:
        def resolve_configuration(self, sha, *, phase):
            assert sha == spec.configuration_sha256 and phase == "observe"
            return binding
    recovered = McpDiscoveryToolAdapter(Registry())
    result = asyncio.run(VerifiedToolOrchestrator(timeline, recovered, clock=lambda: NOW).run(command))
    assert result.status == "verified"
    assert result.proof["observed"] == {}
    assert result.proof["evidence"]["source"] == "mcp_gateway_invocation_response"
    assert result.proof["evidence"]["external_state_verified"] is False
    assert result.proof["evidence"]["observation"] == OUTPUT
    assert len(calls) == len(catalog_calls) == 1

    admission = {"action_name": call.name, "action_call_id": call.call_id,
                 "action_arguments": call.arguments, "conversation_id": _turn().conversation_id,
                 "principal_id": _turn().principal_id, "source_message_id": _turn().message_id,
                 "receipt_output": prepared.receipt_output, "observation_output_key": "result"}
    events = tuple(TimelineEvent(uuid4(), "thread:1", index, f"checkpoint-{index}", "operation",
                                event_type, {"data": data}, canonical_sha256(data), NOW)
                   for index, (event_type, data) in enumerate((
                       ("TOOL_REQUEST_ADMITTED", admission),
                       ("STATE_VERIFIED", {"proof": result.proof}))))
    engine = sa.create_engine("sqlite://")
    try:
        receipt = PostgresExternalConversationTools(engine, adapter.planner(), adapter)._receipt(_turn(), events)
    finally:
        engine.dispose()
    assert receipt.output["result"] == OUTPUT
    assert receipt.output["external_state_verified"] is False
    assert receipt.output["operation_kind"] == "invoke"
    assert receipt.output["status"] == "verified"


def test_missing_invocation_response_after_ambiguous_execution_never_replays(monkeypatch):
    calls, _ = _provider(monkeypatch)
    spec = _invoke_spec()
    adapter = McpGatewayToolAdapter(CONFIG, (spec,))
    asyncio.run(adapter.verify_catalog())
    class CrashBeforeDurableDispatch:
        async def execute(self, request):
            await adapter.execute(request)
            raise asyncio.CancelledError
        observe = adapter.observe
    timeline = Timeline()
    command = _command(spec)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(VerifiedToolOrchestrator(timeline, CrashBeforeDurableDispatch(), clock=lambda: NOW).run(command))
    assert timeline.state.phase == "tool_prepared"
    assert len(calls) == 1
    fresh = McpGatewayToolAdapter(CONFIG, (spec,))
    for missing in (None, ToolDispatchResult(True, None, {})):
        observation = asyncio.run(fresh.observe(command, missing))
        assert observation.disposition == "pending"
        assert "observation" not in observation.evidence
    ticks = iter([0.0, 0.0, 2.0])
    async def no_sleep(_seconds):
        return None
    result = asyncio.run(VerifiedToolOrchestrator(
        timeline, fresh, clock=lambda: NOW, monotonic=lambda: next(ticks), sleep=no_sleep,
    ).run(command))
    assert result.status == "timed_out"
    assert len(calls) == 1
    assert not any(e.event_type == "TOOL_EXECUTED" for e in timeline.recorded)


def test_invocation_rejects_swapped_request_configuration_and_response(monkeypatch):
    calls, _ = _provider(monkeypatch)
    spec = _invoke_spec()
    adapter = McpGatewayToolAdapter(CONFIG, (spec,))
    asyncio.run(adapter.verify_catalog())
    command = _command(spec)
    dispatch = asyncio.run(adapter.execute(command))
    for changed in (
        replace(command, operation_id="another-operation"),
        replace(command, thread_id="another-owner-thread"),
        replace(command, arguments={"message": "ander"}, observation_arguments={"message": "ander"}),
        replace(command, tool_configuration_sha256="a" * 64),
        replace(command, observation_arguments={"message": "ander"}),
        replace(command, expected_proof={"text": "onterecht"}),
    ):
        with pytest.raises(RuntimeError, match="binding differs"):
            asyncio.run(adapter.observe(changed, dispatch))
    for changed in (
        replace(dispatch, details={**dispatch.details, "tool_configuration_sha256": "a" * 64}),
        replace(dispatch, details={**dispatch.details, "invocation_request_sha256": "b" * 64}),
        replace(dispatch, details={**dispatch.details, "operation_kind": "read"}),
        replace(dispatch, details={**dispatch.details, "invocation_response": {"text": "tampered"}}),
        replace(dispatch, provider_ref="wrong-response"),
        replace(dispatch, accepted=False),
    ):
        with pytest.raises(RuntimeError, match="differs"):
            asyncio.run(adapter.observe(command, changed))
    assert len(calls) == 1


def test_invocation_bounds_complete_utf8_result_before_dispatch_is_persisted(monkeypatch):
    calls, _ = _provider(monkeypatch, {"text": "euro: " + "\u20ac" * 22000})
    adapter = McpGatewayToolAdapter(CONFIG, (_invoke_spec(),))
    asyncio.run(adapter.verify_catalog())
    timeline = Timeline()
    result = asyncio.run(VerifiedToolOrchestrator(timeline, adapter, clock=lambda: NOW).run(_command()))
    assert result.status == "failed"
    assert timeline.state.phase == "tool_prepared"
    assert not any(e.event_type == "TOOL_EXECUTED" for e in timeline.recorded)
    assert len(calls) == 1
    assert asyncio.run(adapter.observe(_command(), None)).disposition == "pending"


def test_v4_parser_admits_explicit_invoke_and_rejects_legacy_or_mismatched_modes():
    payload = json.loads(_provider_catalog())
    payload["contract"] = "EvaMcpGatewayProvider/v4"
    tool = payload["tools"][0]
    tool["operation_kind"] = "invoke"
    tool["proof_bindings"] = []
    for phase in ("execute", "observe"):
        tool[f"{phase}_argument_bindings"] = [{"argument_name": "message", "provider_name": "message"}]
    assert parse_mcp_gateway_catalog(json.dumps(payload))[0].operation_kind == "invoke"
    for contract in ("EvaMcpGatewayProvider/v1", "EvaMcpGatewayProvider/v2", "EvaMcpGatewayProvider/v3"):
        with pytest.raises(RuntimeError):
            parse_mcp_gateway_catalog(json.dumps({**payload, "contract": contract}))
    for changes in ({"operation_kind": "readOnlyHint"}, {"operation_kind": "effect"},
                    {"proof_bindings": [{"argument_name": "message", "observed_name": "text"}]},
                    {"observe_tool_name": "different"}, {"observe_catalog_sha256": "b" * 64},
                    {"observe_argument_bindings": []}):
        with pytest.raises(RuntimeError):
            parse_mcp_gateway_catalog(json.dumps({**payload, "tools": [{**tool, **changes}]}))
    for contract in ("EvaMcpGatewayProvider/v1", "EvaMcpGatewayProvider/v2", "EvaMcpGatewayProvider/v3"):
        with pytest.raises(ValueError):
            replace(_invoke_spec(), contract=contract)


def test_v3_read_and_legacy_configuration_hashes_remain_unchanged(monkeypatch):
    assert [_v1_spec().configuration_sha256, _spec().configuration_sha256,
            _read_spec().configuration_sha256] == [
        "833537738f5249290d126d37c5fdc378717b18a14232cd9a280bb1239f4af5ae",
        "623ea8225351a4c4d2d179fdf7f5d865523c947b2890d7304ce88808bf134dbe",
        "a3ec995b0e612045a36ad13788007029ac0997d7f9135703eabfe4fb088e90a5",
    ]
    calls, _ = _provider(monkeypatch)
    spec = _read_spec(execute_catalog_sha256=DIGEST, observe_catalog_sha256=DIGEST)
    adapter = McpGatewayToolAdapter(CONFIG, (spec,))
    asyncio.run(adapter.verify_catalog())
    dispatch = asyncio.run(adapter.execute(_command(spec)))
    assert calls == []
    observed = asyncio.run(adapter.observe(_command(spec), dispatch))
    assert len(calls) == 1
    assert observed.observed == {}
    assert observed.evidence == {"observation": OUTPUT, "observation_sha256": canonical_sha256(OUTPUT),
                                 "source": "mcp_gateway_read"}
