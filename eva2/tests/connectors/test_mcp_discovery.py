from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayArgumentBinding, McpGatewayConfig, McpGatewayProofBinding,
    McpGatewayToolAdapter, McpGatewayToolSpec, mcp_gateway_catalog_binding,
)
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationToolDescriptor, ConversationTurn
from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_discovery import McpDiscoveryToolAdapter, McpExecutionBinding


TURN = ConversationTurn.private("tenant", "installation", "owner", "chat", "message", "Gebruik de dienst.", "2026-09-09T12:00:00Z")
SCHEMA = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False}
WRITE_SCHEMA = {**SCHEMA, "properties": {**SCHEMA["properties"], "value": {"type": "string"}}, "required": ["key", "value"]}


def _row(name, schema):
    return {"name": name, "input_schema": schema, "annotations": {"readOnlyHint": True},
            "enabled": True, "reachable": True, "deprecated": False,
            "integration_type": "MCP", "request_type": "STREAMABLEHTTP", "visibility": "private"}


ROWS = [_row("service.lookup", SCHEMA), _row("service.change", WRITE_SCHEMA)]


def _binding(mode):
    effect = mode == "effect"
    execute = ROWS[1] if effect else ROWS[0]
    observe = ROWS[0]
    schema = WRITE_SCHEMA if effect else SCHEMA
    spec = McpGatewayToolSpec(
        descriptor=ConversationToolDescriptor(f"registered_{mode}", "An unknown configured service tool.", schema),
        execute_tool_name=execute["name"], observe_tool_name=observe["name"],
        execute_catalog_name=execute["name"], observe_catalog_name=observe["name"],
        execute_catalog_sha256=canonical_sha256(mcp_gateway_catalog_binding(execute, contract="EvaMcpGatewayProvider/v3")),
        observe_catalog_sha256=canonical_sha256(mcp_gateway_catalog_binding(observe, contract="EvaMcpGatewayProvider/v3")),
        proof_bindings=(McpGatewayProofBinding("value", "value"),) if effect else (),
        contract="EvaMcpGatewayProvider/v4" if mode == "invoke" else "EvaMcpGatewayProvider/v3", operation_kind=mode,
        execute_argument_bindings=tuple(McpGatewayArgumentBinding(n, n) for n in schema["properties"]),
        observe_argument_bindings=(McpGatewayArgumentBinding("key", "key"),),
    )
    return McpExecutionBinding(spec, McpGatewayConfig("https://gateway.invalid", "private-token"), "connector-1", f"tool-{mode}")


class Registry:
    def __init__(self):
        self.by_tool = {binding.tool_id: binding for binding in (_binding("read"), _binding("effect"))}
        self.by_hash = {b.spec.configuration_sha256: b for b in self.by_tool.values()}
        self.resolved = []
        self.revoked = False

    def resolve_for_turn(self, turn, tool_id):
        assert turn.principal_id == "owner"
        if self.revoked:
            raise ConversationConflict("connector grant revoked")
        return self.by_tool[tool_id]

    def resolve_configuration(self, sha, *, phase):
        binding = self.by_hash[sha]
        if self.revoked and (phase == "execute" or binding.spec.operation_kind == "read"):
            raise ConversationConflict("connector grant revoked")
        self.resolved.append((sha, phase))
        return binding


def _request(prepared):
    return ToolRequest(
        "thread", "operation", "checkpoint", prepared.tool_name, prepared.arguments,
        prepared.expected_proof, observation_tool_name=prepared.observation_tool_name,
        observation_arguments=prepared.observation_arguments,
        tool_configuration_sha256=prepared.tool_configuration_sha256,
    )


def test_metatool_reads_writes_and_resumes_exact_registered_configuration(monkeypatch):
    registry = Registry()
    calls, catalog_reads, state = [], [], {"entry": "before"}
    def request_json(self, method, path, payload):
        if method == "GET":
            catalog_reads.append(path)
            return {"tools": ROWS, "nextCursor": None}
        params = payload["params"]
        calls.append(params)
        args = params["arguments"]
        if params["name"] == "service.change":
            state[args["key"]] = args["value"]
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"value": state[args["key"]]}}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    adapter = McpDiscoveryToolAdapter(registry)
    assert adapter.conversation_tools()[0].name == "use_connector_tool"
    for mode, arguments in (("read", {"key": "entry"}), ("effect", {"key": "entry", "value": "after"})):
        prepared = asyncio.run(adapter.planner().prepare(
            TURN, ConversationActionCall(f"call-{mode}", "use_connector_tool",
                                         {"tool_id": f"tool-{mode}", "arguments_json": json.dumps(arguments)}),
        ))
        assert prepared.receipt_output["connector_id"] == "connector-1"
        assert prepared.receipt_output["tool_id"] == f"tool-{mode}"
        command = _request(prepared)
        dispatch = asyncio.run(adapter.execute(command))
        if mode == "read":
            assert calls == []
            assert prepared.observation_output_key == "result"
        else:
            assert prepared.arguments == {"key": "entry", "value": "after"}
            assert prepared.observation_arguments == {"key": "entry"}
            assert prepared.expected_proof == {"value": "after"}
            assert prepared.observation_output_key is None
            # The current name now points elsewhere. Replay must keep the admitted hash.
            binding = registry.by_tool["tool-effect"]
            new_spec = replace(binding.spec, descriptor=replace(binding.spec.descriptor, description="New generation."))
            registry.by_tool["tool-effect"] = replace(binding, spec=new_spec)
            adapter = McpDiscoveryToolAdapter(registry)  # also exercise an empty post-restart cache
        observed = asyncio.run(adapter.observe(command, dispatch))
        if mode == "read":
            assert observed.evidence["observation"] == {"value": "before"}
            assert observed.observed == {}
        else:
            assert observed.observed == {"value": "after"}
            assert registry.resolved[-1] == (binding.spec.configuration_sha256, "observe")
    assert [c["name"] for c in calls] == ["service.lookup", "service.change", "service.lookup"]
    assert len(catalog_reads) == 3  # first read, first effect, resumed old effect
    count = len(calls)
    registry.by_hash[command.tool_configuration_sha256] = registry.by_tool["tool-effect"]
    with pytest.raises(RuntimeError, match="configuration binding differs"):
        asyncio.run(adapter.observe(command, None))
    registry.revoked = True
    with pytest.raises(ConversationConflict, match="grant revoked"):
        asyncio.run(adapter.execute(command))
    assert len(calls) == count


@pytest.mark.parametrize("raw", ["[]", "{broken}", '{"key":"a","key":"b"}', '{"key":NaN}', "{}" + " " * 65_536])
def test_metatool_rejects_invalid_json_before_registry_or_gateway(raw):
    class UnusedRegistry:
        def resolve_for_turn(self, *_args):
            pytest.fail("invalid JSON reached the registry")
    adapter = McpDiscoveryToolAdapter(UnusedRegistry())
    with pytest.raises(ConversationConflict, match="JSON|arguments"):
        asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            "call", "use_connector_tool", {"tool_id": "tool-read", "arguments_json": raw},
        )))


def test_metatool_enforces_the_registered_input_schema(monkeypatch):
    calls = []
    def request_json(self, method, path, payload):
        calls.append(method)
        return {"tools": ROWS, "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    adapter = McpDiscoveryToolAdapter(Registry())
    with pytest.raises(ConversationConflict, match="fields are invalid"):
        asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            "call", "use_connector_tool", {"tool_id": "tool-read", "arguments_json": '{"key":"entry","extra":true}'},
        )))
    assert calls == ["GET"]


def test_revocation_blocks_dispatch_and_new_reads_but_allows_admitted_effect_readback(monkeypatch):
    registry = Registry()
    calls = []
    def request_json(self, method, path, payload):
        if method == "GET":
            return {"tools": ROWS, "nextCursor": None}
        calls.append(payload["params"])
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"value": "already changed"}}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    adapter = McpDiscoveryToolAdapter(registry)
    commands = {}
    for mode, arguments in (("read", {"key": "entry"}),
                            ("effect", {"key": "entry", "value": "already changed"})):
        prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            f"call-{mode}", "use_connector_tool",
            {"tool_id": f"tool-{mode}", "arguments_json": json.dumps(arguments)},
        )))
        commands[mode] = _request(prepared)
    dispatch = asyncio.run(adapter.execute(commands["effect"]))
    assert calls == [{"name": "service.change", "arguments": {"key": "entry", "value": "already changed"}}]
    registry.revoked = True
    for command in commands.values():
        with pytest.raises(ConversationConflict, match="revoked"):
            asyncio.run(adapter.execute(command))
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.observe(commands["read"], None))
    observation = asyncio.run(adapter.observe(commands["effect"], dispatch))
    assert observation.observed == {"value": "already changed"}
    assert [call["name"] for call in calls] == ["service.change", "service.lookup"]
    assert registry.resolved[-1] == (commands["effect"].tool_configuration_sha256, "observe")


class BrowserPreparation:
    def __init__(self):
        self.calls = []
        self.blocked = False
        self.revision = 7
        self.generation_checks = []
        self.connector_id = "connector-1"

    def matches(self, connector_id):
        return connector_id == self.connector_id

    def session_binding(self, connector_id):
        return connector_id

    async def verify_generation(self, connector_id, revision):
        self.generation_checks.append(revision)
        if self.blocked:
            raise ConversationConflict("owner control")
        if not self.matches(connector_id) or revision != self.revision:
            raise ConversationConflict("browser generation changed")

    async def ensure_available(self, connector_id):
        if not self.matches(connector_id):
            return
        self.calls.append(connector_id)
        if self.blocked:
            raise ConversationConflict("owner control")
        return self.revision


def browser_adapter(monkeypatch, mode="read"):
    registry = Registry()
    binding = _binding(mode)
    registry.by_tool[binding.tool_id] = binding
    registry.by_hash[binding.spec.configuration_sha256] = binding
    browser = BrowserPreparation()
    requests = []
    def request_json(self, method, path, payload):
        requests.append(method)
        if method == "GET":
            return {"tools": ROWS, "nextCursor": None}
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"value": "observed"}}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    return registry, browser, requests, McpDiscoveryToolAdapter(registry, browser_autostart=browser)


def prepare_browser_command(adapter, mode="read"):
    return _request(asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
        "browser-call", "use_connector_tool",
        {"tool_id": "tool-" + mode, "arguments_json": '{"key":"entry"}'},
    ))))


def test_browser_preparation_follows_authority_and_input_validation(monkeypatch):
    registry, browser, requests, adapter = browser_adapter(monkeypatch)
    with pytest.raises(ConversationConflict, match="fields are invalid"):
        asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            "invalid", "use_connector_tool",
            {"tool_id": "tool-read", "arguments_json": '{"key": "entry", "extra": true}'},
        )))
    registry.revoked = True
    with pytest.raises(ConversationConflict, match="revoked"):
        prepare_browser_command(adapter)
    assert browser.calls == requests == []


@pytest.mark.parametrize("change", ["arguments", "observation", "tool", "proof"])
def test_invalid_durable_request_never_starts_browser(monkeypatch, change):
    registry, browser, requests, adapter = browser_adapter(monkeypatch)
    command = prepare_browser_command(adapter)
    browser.calls.clear()
    requests.clear()
    if change == "arguments":
        command = replace(command, arguments={"key": 7}, observation_arguments={"key": 7})
    elif change == "observation":
        command = replace(command, observation_tool_name="other")
    elif change == "tool":
        command = replace(command, tool_name="other")
    else:
        command = replace(command, expected_proof={"invented": True})
    with pytest.raises((ConversationConflict, RuntimeError)):
        asyncio.run(adapter.execute(command))
    assert browser.calls == requests == []


def test_cached_adapter_still_checks_owner_before_new_dispatch(monkeypatch):
    _, browser, requests, adapter = browser_adapter(monkeypatch)
    command = prepare_browser_command(adapter)
    assert browser.calls == ["connector-1"] and requests == ["GET"]
    browser.blocked = True
    with pytest.raises(ConversationConflict, match="owner control"):
        asyncio.run(adapter.execute(command))
    assert browser.calls == ["connector-1", "connector-1"]
    assert requests == ["GET"]


def test_invocation_receipt_observe_never_starts_or_contacts_browser(monkeypatch):
    from eva.adapters.postgres import connector_results
    _, browser, requests, adapter = browser_adapter(monkeypatch, "invoke")
    command = prepare_browser_command(adapter, "invoke")
    dispatch = asyncio.run(adapter.execute(command))
    browser.calls.clear()
    requests.clear()
    browser.blocked = True
    observed = asyncio.run(adapter.observe(command, dispatch))
    assert observed.disposition == "verified"
    assert browser.calls == requests == []
    monkeypatch.setattr(connector_results, "recovered_result", lambda *args: {"value": "observed"})
    recovered = asyncio.run(adapter.observe(command, None))
    assert recovered.disposition == "verified"
    assert browser.calls == requests == []


@pytest.mark.parametrize("mode", ["read", "invoke"])
def test_stored_or_uncertain_results_do_not_start_browser(monkeypatch, mode):
    from eva.adapters.postgres import connector_results
    _, browser, requests, adapter = browser_adapter(monkeypatch, mode)
    command = prepare_browser_command(adapter, mode)
    browser.calls.clear()
    requests.clear()
    browser.blocked = True
    monkeypatch.setattr(connector_results, "recovered_result", lambda *args: {"value": "observed"})
    monkeypatch.setattr(connector_results, "captured_call", lambda *args: {"value": "observed"})
    dispatch = asyncio.run(adapter.execute(command))
    observed = asyncio.run(adapter.observe(command, dispatch))
    assert observed.disposition == "verified"
    assert browser.calls == requests == []
    def uncertain(*args):
        raise RuntimeError("uncertain capture")
    monkeypatch.setattr(connector_results, "recovered_result", uncertain)
    with pytest.raises(RuntimeError, match="uncertain capture"):
        asyncio.run(adapter.execute(command))
    assert browser.calls == requests == []


def test_new_read_after_restart_prepares_browser_but_other_connector_does_not(monkeypatch):
    registry, browser, requests, adapter = browser_adapter(monkeypatch)
    command = prepare_browser_command(adapter)
    browser.calls.clear()
    restarted = McpDiscoveryToolAdapter(registry, browser_autostart=browser)
    assert asyncio.run(restarted.observe(command, None)).disposition == "verified"
    assert browser.calls == ["connector-1"]
    browser.calls.clear()
    binding = registry.by_hash[command.tool_configuration_sha256]
    registry.by_hash[command.tool_configuration_sha256] = replace(binding, connector_id="another-connector")
    asyncio.run(restarted.execute(command))
    assert browser.calls == []


def test_managed_session_is_shared_across_tools_and_provider_instances(monkeypatch):
    registry, browser, _, adapter = browser_adapter(monkeypatch)
    read = prepare_browser_command(adapter)
    effect = _request(asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
        "write", "use_connector_tool",
        {"tool_id": "tool-effect", "arguments_json": '{"key":"entry","value":"new"}'},
    ))))
    read_config = adapter._cache[read.tool_configuration_sha256][0]
    effect_config = adapter._cache[effect.tool_configuration_sha256][0]
    assert read_config.session_id == effect_config.session_id
    assert len(read_config.session_id) == 64
    other_process = McpDiscoveryToolAdapter(registry, browser_autostart=BrowserPreparation())
    other_read = prepare_browser_command(other_process)
    assert other_process._cache[other_read.tool_configuration_sha256][0].session_id == read_config.session_id
    assert registry.by_tool["tool-read"].config.session_id is None
    assert "session" not in read.arguments
    assert read_config.session_id not in repr(read_config)


def test_managed_session_rotates_for_revision_token_and_owner_connector(monkeypatch):
    registry, browser, _, adapter = browser_adapter(monkeypatch)
    def session():
        command = prepare_browser_command(adapter)
        return adapter._cache[command.tool_configuration_sha256][0].session_id
    sessions = [session()]
    browser.revision += 1
    sessions.append(session())
    original = registry.by_tool["tool-read"]
    changed = replace(original, config=replace(original.config, bearer_token="rotated-private-token"))
    registry.by_tool["tool-read"] = changed
    sessions.append(session())
    browser.connector_id = "another-owner-derived-connector"
    registry.by_tool["tool-read"] = replace(changed, connector_id=browser.connector_id)
    sessions.append(session())
    assert len(set(sessions)) == 4
    assert all(len(value) == 64 for value in sessions)


def test_unmanaged_connector_keeps_default_gateway_transport(monkeypatch):
    registry, browser, requests, adapter = browser_adapter(monkeypatch)
    browser.connector_id = "different-managed-browser"
    command = prepare_browser_command(adapter)
    config = adapter._cache[command.tool_configuration_sha256][0]
    assert config.session_id is None
    assert browser.calls == []
    assert requests == ["GET"]


def test_browser_waits_for_real_gateway_health_before_admitting_and_caches(monkeypatch):
    registry, browser, _, adapter = browser_adapter(monkeypatch)
    reads, waits = [], []
    async def sleep(seconds):
        waits.append(seconds)
    def request_json(self, method, path, payload):
        assert method == "GET" and payload is None
        reads.append(path)
        return {"tools": [{**row, "reachable": len(reads) > 1} for row in ROWS],
                "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    command = prepare_browser_command(adapter)
    assert len(reads) == 2 and waits == [5]
    assert browser.generation_checks == [7, 7, 7]
    assert registry.resolved == [(command.tool_configuration_sha256, "execute")] * 3
    assert adapter._cache[command.tool_configuration_sha256][1]._verified_input_schemas
    prepare_browser_command(adapter)
    assert len(reads) == 2 and waits == [5]  # Healthy generation stays fast.


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"deprecated": True}, {"integration_type": "REST"},
    {"visibility": "unknown"}, {"input_schema": None}, {"reachable": None},
    {"reachable": "false"},
])
def test_browser_does_not_wait_for_unsafe_or_unknown_catalog_fields(monkeypatch, change):
    _, browser, _, adapter = browser_adapter(monkeypatch)
    reads = []
    def request_json(self, method, path, payload):
        reads.append(method)
        return {"tools": [{**row, "reachable": False, **change} for row in ROWS],
                "nextCursor": None}
    async def forbidden_sleep(_seconds):
        pytest.fail("unsafe catalog was treated as a pending health check")
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", forbidden_sleep)
    with pytest.raises(RuntimeError, match="not safely executable"):
        prepare_browser_command(adapter)
    assert reads == ["GET"] and not adapter._cache
    assert browser.generation_checks == []


@pytest.mark.parametrize("initially_unreachable", [False, True])
def test_browser_health_never_bypasses_sealed_schema(monkeypatch, initially_unreachable):
    _, _, _, adapter = browser_adapter(monkeypatch)
    reads = []
    async def sleep(_seconds):
        pass
    def request_json(self, method, path, payload):
        reads.append(method)
        changed = {**SCHEMA, "properties": {"key": {"type": "integer"}}}
        return {"tools": [{**ROWS[0], "input_schema": changed,
                           "reachable": not initially_unreachable or len(reads) > 1}],
                "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    with pytest.raises(RuntimeError, match="sealed catalog"):
        prepare_browser_command(adapter)
    assert reads == ["GET"] * (2 if initially_unreachable else 1)
    assert not adapter._cache


@pytest.mark.parametrize("change", ["owner", "revision", "revoked", "binding", "cancel"])
@pytest.mark.parametrize("when", ["waiting", "health_returned"])
def test_browser_health_wait_rechecks_generation_grant_and_cancellation(monkeypatch, change, when):
    from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
    registry, browser, _, adapter = browser_adapter(monkeypatch)
    signal = CancellationSignal()
    reads = []
    def change_authority():
        if change == "owner":
            browser.blocked = True
        elif change == "revision":
            browser.revision += 2
        elif change == "revoked":
            registry.revoked = True
        elif change == "binding":
            old = registry.by_tool["tool-read"]
            registry.by_hash[old.spec.configuration_sha256] = replace(
                old, config=replace(old.config, bearer_token="rotated-token"))
        else:
            signal.cancel()
    async def sleep(_seconds):
        if when == "waiting":
            change_authority()
    def request_json(self, method, path, payload):
        assert method == "GET"
        reads.append(method)
        ready = len(reads) > 1
        if ready and when == "health_returned":
            change_authority()
        return {"tools": [{**row, "reachable": ready} for row in ROWS], "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    with cancellation_scope(signal), pytest.raises((ConversationConflict, WorkInterrupted)):
        prepare_browser_command(adapter)
    assert len(reads) == (1 if when == "waiting" else 2)
    assert not adapter._cache


def test_browser_gateway_health_wait_is_bounded_by_pinned_cycle_and_existing_io(monkeypatch):
    _, _, _, adapter = browser_adapter(monkeypatch)
    actual_timeout, actual_sleep = asyncio.timeout, asyncio.sleep
    budgets, reads = [], []
    def timeout(seconds):
        budgets.append(seconds)
        return actual_timeout(0.01)
    async def sleep(_seconds):
        await actual_sleep(0.001)
    def request_json(self, method, path, payload):
        assert method == "GET"
        reads.append(method)
        return {"tools": [{**row, "reachable": False} for row in ROWS], "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.timeout", timeout)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    with pytest.raises(ConversationConflict, match="gateway health is not ready"):
        prepare_browser_command(adapter)
    assert budgets == [60 + _binding("read").config.request_timeout_seconds]
    assert reads and not adapter._cache


def test_unmanaged_unreachable_catalog_is_not_waited_or_admitted(monkeypatch):
    _, browser, _, adapter = browser_adapter(monkeypatch)
    browser.connector_id = "another-browser"
    reads = []
    def request_json(self, method, path, payload):
        reads.append(method)
        return {"tools": [{**row, "reachable": False} for row in ROWS], "nextCursor": None}
    async def forbidden_sleep(_seconds):
        pytest.fail("unmanaged connector entered browser readiness")
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", forbidden_sleep)
    with pytest.raises(RuntimeError, match="unreachable"):
        prepare_browser_command(adapter)
    assert reads == ["GET"] and not adapter._cache and not browser.calls


def test_browser_health_cycle_keeps_paginated_reads_below_gateway_rate_limit(monkeypatch):
    _, _, _, adapter = browser_adapter(monkeypatch)
    elapsed = 0
    reads = []
    async def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds
    def request_json(self, method, path, payload):
        assert method == "GET" and payload is None
        reads.append((elapsed, path))
        # Catalog pages share ContextForge's 100 rpm /tools allowance.
        assert sum(elapsed - at < 60 for at, _ in reads) <= 100
        if "cursor=" not in path:
            return {"tools": [ROWS[1]], "nextCursor": "second-page"}
        return {"tools": [{**ROWS[0], "reachable": elapsed >= 60}],
                "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    command = prepare_browser_command(adapter)
    assert elapsed == 60
    assert len(reads) == 26
    assert adapter._cache[command.tool_configuration_sha256][1]._verified_input_schemas


def test_browser_health_wait_does_not_retry_rate_limit_response(monkeypatch):
    _, _, _, adapter = browser_adapter(monkeypatch)
    reads = []
    async def sleep(_seconds):
        pass
    def request_json(self, method, path, payload):
        assert method == "GET"
        reads.append(method)
        if len(reads) == 2:
            raise RuntimeError("MCP gateway request failed (429)")
        return {"tools": [{**row, "reachable": False} for row in ROWS],
                "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    with pytest.raises(RuntimeError, match="429"):
        prepare_browser_command(adapter)
    assert reads == ["GET", "GET"] and not adapter._cache


def test_browser_health_poll_pause_is_immediately_cancellable(monkeypatch):
    from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
    _, _, _, adapter = browser_adapter(monkeypatch)
    real_sleep = asyncio.sleep
    reached_pause = asyncio.Event()
    signal = CancellationSignal()
    reads = []
    async def sleep(seconds):
        reached_pause.set()
        await real_sleep(seconds)
    def request_json(self, method, path, payload):
        reads.append(method)
        return {"tools": [{**row, "reachable": False} for row in ROWS],
                "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    monkeypatch.setattr("eva.providers.mcp_discovery.asyncio.sleep", sleep)
    async def run():
        with cancellation_scope(signal):
            task = asyncio.create_task(adapter.planner().prepare(TURN, ConversationActionCall(
                "cancel-wait", "use_connector_tool",
                {"tool_id": "tool-read", "arguments_json": '{"key":"entry"}'})))
            await reached_pause.wait()
            signal.cancel()
            with pytest.raises(WorkInterrupted):
                await asyncio.wait_for(task, 0.5)
    asyncio.run(run())
    assert reads == ["GET"] and not adapter._cache
