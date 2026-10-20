"""Native source identity and narrowly certified historical status refusal."""
import asyncio
from copy import deepcopy
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.initiative_execution import NativeInitiativeGuard
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn
from eva.application.initiative_execution import initiative_execution
from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_autonomy import _SourceTurnLoader
from eva.providers.mcp_registration import McpRegistrationToolAdapter, _source


NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
TURN = ConversationTurn.private("native-status", "home", "owner", "initiative-task:fixture",
    "native-message", "Original durable native task", "2026-10-20T11:00:00Z")
REFUSAL = hashlib.sha256(
    b"ConversationConflict: native initiative is not an owner connector instruction").hexdigest()


def fixture(*, explicit=True):
    namespace = BackgroundExecutionScope(TURN, "original-execution").namespace
    guard = NativeInitiativeGuard.__new__(NativeInitiativeGuard)
    guard.turn, guard.resume_work = TURN, None
    guard.work_spec = SimpleNamespace(created_at="2026-10-20T12:00:01Z")
    guard.check = Mock()
    guard.explicit_resume_binding = Mock(return_value={
        "work_id": "successor", "spec_sha256": "b" * 64, "parent_task_id": "old-work",
        "parent_spec_sha256": "c" * 64, "source_ingress_sha256": "d" * 64,
        "execution_namespace": namespace, "recovery_ingress_sha256": "e" * 64} if explicit else None)
    authority = SimpleNamespace(configuration_sha256="a" * 64, authorize_turn=Mock(),
        status=Mock(return_value={"active": False, "revoked": False}))
    loader = _SourceTurnLoader.__new__(_SourceTurnLoader)
    loader._authority, loader._timeline = authority, Mock()
    client, registry = Mock(), Mock()
    adapter = McpRegistrationToolAdapter(client, registry, authority, "owner-key", loader)
    return SimpleNamespace(guard=guard, authority=authority, loader=loader,
        client=client, registry=registry, adapter=adapter, namespace=namespace)


def request(adapter, turn=TURN):
    prepared = asyncio.run(adapter.planner().prepare(turn, ConversationActionCall(
        "status-call", "manage_connected_services", {"action": "status"})))
    accepted = {f.name for f in fields(ToolRequest)}
    return ToolRequest(thread_id="test", operation_id="test", checkpoint_prefix="test",
        **{f.name: getattr(prepared, f.name) for f in fields(prepared) if f.name in accepted})


def chain(f, turn=TURN, occurred_at=NOW):
    command = request(f.adapter, turn)
    admission = {"action_name": command.tool_name, "action_call_id": "status-call",
        "action_arguments": {"action": "status"}, "receipt_output": {}, "step_index": 3,
        "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
        "source_message_id": turn.message_id, "execution_namespace": f.namespace,
        "request_tool_name": command.tool_name, "request_arguments": command.arguments,
        "request_expected_proof": command.expected_proof,
        "request_observation_tool_name": command.observation_tool_name,
        "request_observation_arguments": command.observation_arguments,
        "request_tool_configuration_sha256": command.tool_configuration_sha256,
        "request_execution_timeout_seconds": command.execution_timeout_seconds,
        "request_verification_timeout_seconds": command.verification_timeout_seconds,
        "observation_output_key": "result"}
    prepared = {"tool_name": command.tool_name, "arguments": command.arguments,
        "expected_proof": command.expected_proof,
        "observation_tool_name": command.observation_tool_name,
        "observation_arguments": command.observation_arguments,
        "tool_configuration_sha256": command.tool_configuration_sha256}
    operation = PostgresExternalConversationTools._historical_operation_id(
        turn, command.tool_name, admission, 3)
    events = []
    for i, (kind, data, phase, status, suffix) in enumerate([
            ("TOOL_REQUEST_ADMITTED", admission, "admitted", "active", "admitted"),
            ("TOOL_PREPARED", prepared, "tool_prepared", "waiting_for_proof", "prepared"),
            ("RECOVERY_FAILED", {"retry_count": 1, "error_fingerprint": REFUSAL},
             "tool_prepared", "failed", "recovery:1")], 1):
        checkpoint_id = f"checkpoint:{operation}:{suffix}"
        payload = {"data": deepcopy(data), "checkpoint": {"phase": phase, "status": status,
            "revision": i, "operation_id": operation, "checkpoint_id": checkpoint_id,
            "updated_at": occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_error_fingerprint": REFUSAL if i == 3 else None}}
        events.append(TimelineEvent(uuid4(), "tool:" + operation, i, checkpoint_id,
            operation, kind, payload, canonical_sha256(payload), occurred_at))
    return tuple(events)


def test_native_loader_returns_same_turn_with_checks_on_both_sides_of_owner_authority():
    f = fixture()
    order = []
    f.guard.check.side_effect = lambda turn: order.append(("guard", turn))
    f.authority.authorize_turn.side_effect = lambda turn: order.append(("authority", turn))
    with initiative_execution(f.guard):
        restored = f.loader(_source(TURN))
    assert restored is TURN
    assert order == [("guard", TURN), ("authority", TURN), ("guard", TURN)]
    f.loader._timeline.assert_not_called()


@pytest.mark.parametrize("fault", ["missing", "other_guard", "subclass", "admission_only",
    "principal", "audience", "message", "scope", "stopped", "authority", "stop_during_check"])
def test_loader_never_invents_native_or_owner_authority(fault):
    f = fixture()
    source = _source(TURN)
    guard = f.guard
    if fault == "missing": guard = None
    if fault == "other_guard": guard = SimpleNamespace(turn=TURN, check=Mock())
    if fault == "subclass":
        class Other(NativeInitiativeGuard):
            pass
        guard.__class__ = Other
    if fault == "admission_only": guard.resume_work = object()
    if fault == "principal": source["principal_id"] = "foreign"
    if fault == "audience": source["audience"] = {"kind": "household", "principal_id": "owner"}
    if fault == "message": source["message_id"] = "foreign"
    if fault == "scope": source["installation_id"] = "foreign"
    if fault == "stopped": guard.check.side_effect = ConversationConflict("stopped")
    if fault == "authority": f.authority.authorize_turn.side_effect = ConversationConflict("revoked")
    if fault == "stop_during_check": guard.check.side_effect = [None, ConversationConflict("stopped")]
    with initiative_execution(guard), pytest.raises(ConversationConflict):
        f.loader(source)
    assert f.client.mock_calls == [] and f.registry.mock_calls == []


def test_current_native_status_uses_actual_turn_without_changing_effect_guard():
    f = fixture()
    command = request(f.adapter)
    with initiative_execution(f.guard):
        proof = asyncio.run(f.adapter.observe(command, None))
    assert proof.disposition == "verified"
    assert proof.evidence["observation"] == {"active": False, "revoked": False}
    assert all(call.args == (TURN,) for call in f.authority.authorize_turn.call_args_list)
    f.authority.status.assert_called_once_with(TURN)
    assert f.client.mock_calls == [] and f.registry.mock_calls == []


def test_old_status_failure_is_only_context_and_no_old_operation_is_executed():
    f = fixture()
    events = chain(f)
    original = deepcopy(events)
    with initiative_execution(f.guard):
        proof = f.adapter.recoverable_pre_dispatch_refusal(TURN, events)
    evidence = proof["evidence"]
    assert evidence["source"] == "native_connector_status_pre_dispatch_refusal"
    assert evidence["operation_kind"] == "read" and evidence["provider_execution"] == "not_started"
    assert evidence["scope"] == "retained_connector_status_request_only"
    assert evidence["terminal_event_sha256"] == events[-1].payload_sha256
    assert evidence["resume_binding"]["execution_namespace"] == f.namespace
    assert events == original and events[-1].event_type == "RECOVERY_FAILED"
    assert f.client.mock_calls == [] and f.registry.mock_calls == []
    f.authority.status.assert_not_called()


@pytest.mark.parametrize("fault", ["stop", "pause", "resume", "discover", "research",
    "source", "audience", "namespace", "config", "prepared", "expected", "result",
    "fingerprint", "hash", "operation", "checkpoint", "sequence", "order", "future",
    "executed", "verified", "loader", "authority", "not_explicit", "other_guard"])
def test_only_exact_sealed_refused_status_is_classified(fault):
    f = fixture(explicit=fault != "not_explicit")
    events = list(chain(f))
    admission, prepared, failed = [event.payload["data"] for event in events]
    if fault in {"stop", "pause", "resume"}: admission["action_arguments"] = {"action": fault}
    if fault == "discover": admission["action_name"] = "discover_connector_tools"
    if fault == "research": admission["action_name"] = "research_current_options"
    if fault == "source": admission["request_arguments"]["source"]["message_id"] = "other"
    if fault == "audience": admission["request_arguments"]["source"]["audience"]["kind"] = "household"
    if fault == "namespace": admission["execution_namespace"] = "background-work:" + "f" * 64
    if fault == "config": admission["request_tool_configuration_sha256"] = "f" * 64
    if fault == "prepared": prepared["arguments"]["action"] = "stop"
    if fault == "expected": prepared["expected_proof"] = {"active": True}
    if fault == "result": failed["result"] = {}
    if fault == "fingerprint": failed["error_fingerprint"] = "f" * 64
    if fault == "operation": events[1].payload["checkpoint"]["operation_id"] = "other"
    if fault == "checkpoint": events[-1].payload["checkpoint"]["status"] = "verified"
    if fault == "sequence": events[1] = replace(events[1], sequence=7)
    if fault == "order": events[1] = replace(events[1], occurred_at=NOW - timedelta(seconds=1))
    if fault == "future": events[-1] = replace(events[-1], occurred_at=NOW + timedelta(seconds=2))
    if fault == "executed": events[1] = replace(events[1], event_type="TOOL_EXECUTED")
    if fault == "verified": events[-1] = replace(events[-1], event_type="STATE_VERIFIED")
    if fault == "loader": f.adapter.load_source_turn = lambda source: TURN
    if fault == "authority": f.loader._authority = SimpleNamespace()
    for i, event in enumerate(events):
        events[i] = replace(event, payload_sha256=canonical_sha256(event.payload))
    if fault == "hash": events[-1] = replace(events[-1], payload_sha256="f" * 64)
    guard = SimpleNamespace(turn=TURN) if fault == "other_guard" else f.guard
    with initiative_execution(guard):
        assert f.adapter.recoverable_pre_dispatch_refusal(TURN, tuple(events)) is None
    assert f.client.mock_calls == [] and f.registry.mock_calls == []
    f.authority.status.assert_not_called()


def test_classification_preserves_stop_and_current_owner_refusal():
    for reason in ("stopped", "owner revoked"):
        f = fixture()
        events = chain(f)
        if reason == "stopped": f.guard.check.side_effect = ConversationConflict(reason)
        else: f.authority.authorize_turn.side_effect = ConversationConflict(reason)
        with initiative_execution(f.guard), pytest.raises(ConversationConflict, match=reason):
            f.adapter.recoverable_pre_dispatch_refusal(TURN, events)
        assert f.client.mock_calls == [] and f.registry.mock_calls == []


def test_real_native_guard_source_stop_and_recovery_binding(native_digital):
    from tests.postgres.test_native_research_explicit_read_continuation import resumed
    from tests.postgres.test_owner_initiatives import NOW as native_now
    f = native_digital
    _, successor = resumed(f)
    guard, turn = f.auth.bind_work(successor.spec)
    authority = SimpleNamespace(configuration_sha256="a" * 64, authorize_turn=Mock(),
        scope=f.control._scope)
    loader = _SourceTurnLoader(f.store._engine, authority)
    adapter = McpRegistrationToolAdapter(Mock(), Mock(), authority, "owner-key", loader)
    with background_execution(BackgroundExecutionScope(turn, successor.spec.execution_id)), initiative_execution(guard):
        assert loader(_source(turn)) is turn
        binding = guard.explicit_resume_binding(turn)
        assert binding["work_id"] == successor.spec.work_id
        # Restoring source identity does not add a capability to this fixture's
        # deliberately narrower admitted grant.
        assert guard.action_allowed("manage_connected_services") is False
        events = chain(SimpleNamespace(adapter=adapter, namespace=binding["execution_namespace"]),
            turn, native_now)
        proof = adapter.recoverable_pre_dispatch_refusal(turn, events)
        assert proof["evidence"]["resume_binding"] == binding
        assert proof["evidence"]["provider_execution"] == "not_started"
        f.control.stop(f.claim.intent_id, now=native_now)
        with pytest.raises(RuntimeError):
            loader(_source(turn))
        with pytest.raises(RuntimeError):
            adapter.recoverable_pre_dispatch_refusal(turn, events)


# Reuse the existing isolated PostgreSQL fixture; no provider or production store.
from tests.postgres.test_native_initiative_digital_work import native_digital  # noqa: E402, F401
from tests.postgres.test_owner_initiatives import costless_native  # noqa: E402, F401
