"""Shared saved-result access never borrows private connector permissions."""
from contextlib import nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.scoped_connector_results import ScopedConnectorResults
from eva.adapters.postgres.connector_results import ConnectorResults
from eva.application.conversation import ConversationActionCall, ConversationConflict
from tests.connectors.test_connector_results import setup, TURN
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference

def household(principal):
    return MemoryAudience.household(principal_id=principal, household_id="home",
        consent_ref=HouseholdConsentReference(principal, "home", "household-consent:test"))


def harness():
    adapter, command, calls, capture = setup({"text": "saved " * 2000})
    turn = replace(TURN, audience=household(TURN.principal_id),
        conversation_id="household:home:conversation:test",
        message_id="household:home:conversation:test:message", project_id=None)
    capture = ConnectorResults(capture.timeline, turn, lambda _: None)
    output = capture.call(command, "execute", lambda: {"text": "saved " * 2000})
    manifest = capture.manifest(command, "execute", output)
    private_calls = []
    private = SimpleNamespace(
        available_for=lambda t: t.audience.kind == "private_person",
        execute=lambda *a, **k: private_calls.append((a, k)) or "private-result",
        active_context=lambda t: ("private context",),
        with_preparation_call_resolver=lambda resolver: private,
    )
    def authorize(t):
        if t.audience.kind != "household" or t.audience.principal_id != t.principal_id:
            raise ConversationConflict("wrong audience")
    route = ScopedConnectorResults.build(None, private, authorize)
    local = route._local
    local._timeline = lambda t: capture.timeline
    local._execution_lock = lambda key: nullcontext()
    stopped = []
    def running(t):
        authorize(t)
        if stopped:
            raise ConversationConflict("stopped")
    local._ensure_running = running
    call = ConversationActionCall("local-page", "use_connector_tool", {
        "tool_id": manifest["reader"]["tool_id"],
        "arguments_json": json.dumps({"pointer": "/text", "offset": 2048, "limit": 100, "query": None})})
    return route, turn, call, private_calls, stopped, capture


def test_household_reads_replays_and_resumes_own_bytes_without_private_dispatch():
    route, turn, call, private, _, _ = harness()
    assert route.available_for(turn)
    assert route.active_context(turn) == ()
    receipt = route.execute(turn, call, step_index=2)
    assert receipt.output["result"]["text"] == ("saved " * 2000)[2048:2148]
    assert route.execute(turn, call, step_index=2) == receipt
    assert route.pending(turn, step_index=2) == receipt
    assert route.with_preparation_call_resolver(lambda *a: None).execute(turn, call, step_index=2) == receipt
    assert private == []


@pytest.mark.parametrize("identifier", ["remote:tool", "eva-stored-result:" + "a"*64 + ":" + "b"*64])
def test_household_rejects_remote_and_absent_readers(identifier):
    route, turn, call, private, _, _ = harness()
    call = replace(call, arguments={**call.arguments, "tool_id": identifier})
    with pytest.raises((ConversationConflict, KeyError)):
        route.execute(turn, call, step_index=2)
    assert private == []


@pytest.mark.parametrize("field", ["message_id", "conversation_id", "principal_id", "tenant_id", "installation_id"])
def test_saved_result_is_bound_to_exact_turn(field):
    route, turn, call, private, _, _ = harness()
    value = turn.message_id + "different" if field == "message_id" else "different"
    changes = {field: value}
    if field == "conversation_id":
        changes.update(conversation_id=turn.conversation_id + "different",
                       message_id=turn.conversation_id + "different:message")
    if field == "principal_id":
        changes["audience"] = household("different")
    changed = replace(turn, **changes)
    with pytest.raises(ConversationConflict):
        route.execute(changed, call, step_index=2)
    assert private == []


def test_hash_tampering_and_unbound_audience_fail():
    route, turn, call, _, _, _ = harness()
    reader = call.arguments["tool_id"]
    bad = replace(call, arguments={**call.arguments, "tool_id": reader[:-64] + "0"*64})
    with pytest.raises(ConversationConflict):
        route.execute(turn, bad, step_index=2)
    changed = replace(turn, audience=replace(turn.audience,
        household_consent_ref=replace(turn.audience.household_consent_ref,
                                     operation_id="household-consent:other")))
    with pytest.raises(ConversationConflict):
        route.execute(changed, call, step_index=3)


@pytest.mark.parametrize("existing", [False, True])
def test_stop_blocks_fresh_read_replay_and_pending(existing):
    route, turn, call, _, stopped, _ = harness()
    if existing:
        route.execute(turn, call, step_index=2)
    stopped.append(True)
    with pytest.raises(ConversationConflict, match="stopped"):
        route.execute(turn, call, step_index=2)
    with pytest.raises(ConversationConflict, match="stopped"):
        route.pending(turn, step_index=2)


def test_household_cannot_resume_an_external_admission():
    route, turn, _, private, _, capture = harness()
    thread = route._local._thread_id(turn, "use_connector_tool", 2)
    capture.timeline.rows[thread] = [SimpleNamespace(event_type="TOOL_REQUEST_ADMITTED")]
    with pytest.raises(ConversationConflict, match="cannot resume"):
        route.pending(turn, step_index=2)
    assert private == []


def test_private_dispatch_still_uses_original_authority_route():
    route, _, call, private, _, _ = harness()
    assert route.execute(TURN, call, step_index=2) == "private-result"
    assert len(private) == 1
    assert route.active_context(TURN) == ("private context",)


def test_interrupted_local_selection_resumes_without_external_dispatch():
    route, turn, call, private, _, capture = harness()
    expected = route.execute(turn, call, step_index=2)
    thread = route._local._thread_id(turn, "use_connector_tool", 2)
    capture.timeline.rows[thread] = capture.timeline.rows[thread][:1]
    assert route.pending(turn, step_index=2) == expected
    assert private == []


def test_saved_bytes_cannot_move_into_another_execution_namespace():
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    route, turn, call, private, _, _ = harness()
    with background_execution(BackgroundExecutionScope(turn, "another-work", lambda _: None)):
        with pytest.raises(ConversationConflict, match="owner or request differs"):
            route.execute(turn, call, step_index=2)
    assert private == []


def test_invalid_local_selection_can_be_corrected_without_new_source_call():
    route, turn, call, private, stopped, _ = harness()
    bad = replace(call, arguments={**call.arguments,
        "arguments_json": json.dumps({"pointer": "/absent", "offset": 0, "limit": 100, "query": None})})
    receipt = route.execute(turn, bad, step_index=2)
    assert receipt.output["status"] == "selection_invalid"
    assert route.can_continue_after(turn, receipt)
    corrected = route.execute(turn, replace(call, call_id="corrected-page"), step_index=3)
    assert corrected.output["status"] == "verified"
    assert not route.can_continue_after(turn, corrected)
    assert not route.can_continue_after(turn, replace(receipt, output={**receipt.output, "source": "external"}))
    stopped.append(True)
    with pytest.raises(ConversationConflict, match="stopped"):
        route.can_continue_after(turn, receipt)
    assert private == []


@pytest.mark.parametrize("reader_change", [lambda reader: reader[:-1], lambda reader: "eva-stored-result:invalid"])
def test_malformed_reader_is_durable_recoverable_without_source_or_authority_bypass(reader_change):
    route, turn, call, private, stopped, capture = harness()
    bad = replace(call, arguments={**call.arguments, "tool_id": reader_change(call.arguments["tool_id"])})
    receipt = route.execute(turn, bad, step_index=2)
    assert receipt.output["status"] == "selection_invalid"
    assert receipt.output["external_state_verified"] is False
    assert "exact" in receipt.output["error"]
    assert route.can_continue_after(turn, receipt)
    assert route.execute(turn, bad, step_index=2) == receipt
    assert route.pending(turn, step_index=2) == receipt
    assert not any(event.event_type == "CONNECTOR_SELECTION_READ" for events in capture.timeline.rows.values() for event in events)
    corrected = route.execute(turn, replace(call, call_id="corrected-reader"), step_index=3)
    assert corrected.output["status"] == "verified"
    assert private == []
    stopped.append(True)
    with pytest.raises(ConversationConflict, match="stopped"):
        route.execute(turn, replace(bad, call_id="after-stop"), step_index=4)
