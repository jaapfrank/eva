"""Indexed stored-result replay through PostgreSQL and the real conversation loop."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.connector_results import ConnectorResults, result_capture
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import (ConversationActionCall, ConversationConflict,
    ConversationModelResult, ConversationToolPreparation, IdentityProfile, LivingConversation)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
from tests.connectors.test_connector_results import setup
from tests.connectors.test_mcp_discovery import TURN
from tests.postgres.test_operational_timeline import timeline_engine
from tests.application.test_conversation import _Memory


@pytest.mark.parametrize("invalid_first", [False, True])
def test_local_read_then_effect_replays_every_slot_without_repeating_effect(timeline_engine, invalid_first):
    turn = replace(TURN, message_id="indexed:" + str(invalid_first))
    effects, dispatches = [], []
    class Planner:
        action_names = ("use_connector_tool", "write_document")
        async def prepare(self, turn, call):
            assert call.name == "write_document"
            return ConversationToolPreparation(tool_name="fixture.write", arguments=call.arguments,
                expected_proof={"stored": True}, receipt_output={"saved": True})
    class Provider:
        supports_stored_results = True
        async def execute(self, request):
            effects.append(request.operation_id)
            return ToolDispatchResult(True, request.operation_id, {})
        async def observe(self, request, dispatch):
            return ToolProofObservation("verified", {"stored": True}, {})
    def route():
        return PostgresExternalConversationTools(timeline_engine, Planner(), Provider())
    scope = BackgroundExecutionScope(turn, "work:stable:" + str(invalid_first), lambda t: dispatches.append(t))
    adapter, request, fetches, _ = setup({"text": "stored source " + "x" * 90000})
    with background_execution(scope):
        timeline = PostgresOperationalTimeline(timeline_engine, external_timeline_scope(turn.tenant_id, turn.installation_id, "test"))
        capture = ConnectorResults(timeline, turn, lambda t: None)
        with result_capture(capture):
            reader = asyncio.run(adapter.execute(request)).details["invocation_response"]["reader"]["tool_id"]
        def read_call(call_id, limit):
            return ConversationActionCall(call_id, "use_connector_tool", {"tool_id": reader,
                "arguments_json": json.dumps({"pointer": "/text", "offset": 0, "limit": limit, "query": None})})
        initial = route()
        calls = [read_call("bad", 9000), read_call("good", 100)] if invalid_first else [read_call("good", 100)]
        receipts = [initial.execute(turn, call, step_index=index) for index, call in enumerate(calls)]
        write = ConversationActionCall("write", "write_document", {"content": "one effect"})
        receipts.append(initial.execute(turn, write, step_index=len(calls)))
        assert len(effects) == 1
        before = len(dispatches)
        restarted = route()
        for index, receipt in enumerate(receipts):
            assert restarted.pending(turn, step_index=index) == receipt
        actions = BackgroundConnectorActions((restarted,), Planner.action_names, lambda t: None, 6)
        class AfterRestart:
            def generate(self, *args, **kwargs):
                assert kwargs["completed_action"].call == write
                assert len(kwargs.get("prior_actions", ())) == len(calls)
                return ConversationModelResult("Existing effect verified.", "controlled")
        reply = LivingConversation(_Memory(), AfterRestart(), IdentityProfile("Eva", "Helpful", ("Be honest",)), actions).respond(turn)
        assert reply.text == "Existing effect verified."
        assert len(effects) == 1 and len(fetches) == 1 and len(dispatches) == before
        assert actions.calls == 0
        with pytest.raises(ConversationConflict):
            restarted.execute(turn, read_call("different", 100), step_index=len(calls))
        with pytest.raises(ConversationConflict):
            restarted.execute(turn, read_call("different", 99), step_index=0)
        assert len(effects) == 1


def test_read_crash_after_page_record_recovers_index_without_reinvocation(timeline_engine, monkeypatch):
    turn = replace(TURN, message_id="indexed:crash")
    adapter, command, fetches, _ = setup({"text": "x" * 90000})
    with background_execution(BackgroundExecutionScope(turn, "work:crash", lambda t: None)):
        timeline = PostgresOperationalTimeline(timeline_engine, external_timeline_scope(turn.tenant_id, turn.installation_id, "test"))
        capture = ConnectorResults(timeline, turn, lambda t: None)
        with result_capture(capture):
            reader = asyncio.run(adapter.execute(command)).details["invocation_response"]["reader"]["tool_id"]
        def route():
            return PostgresExternalConversationTools(timeline_engine, SimpleNamespace(action_names=("use_connector_tool",)),
                                                    SimpleNamespace(supports_stored_results=True))
        call = ConversationActionCall("read", "use_connector_tool", {"tool_id": reader,
            "arguments_json": json.dumps({"pointer": "/text", "offset": 0, "limit": 100, "query": None})})
        original = PostgresOperationalTimeline.transition
        def interrupt(self, **kwargs):
            if kwargs.get("event_type") == "CONNECTOR_SELECTION_COMPLETED":
                raise RuntimeError("controlled crash after local page save")
            return original(self, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(PostgresOperationalTimeline, "transition", interrupt)
            with pytest.raises(RuntimeError, match="controlled crash"):
                route().execute(turn, call, step_index=4)
        recovered = route().pending(turn, step_index=4)
        assert recovered.call == call and recovered.output["status"] == "verified"
        assert recovered.output["result"]["text"] == "x" * 100
        assert route().pending(turn, step_index=4) == recovered
        assert len(fetches) == 1


@pytest.mark.parametrize("changed_boundary", ["audience", "actor", "execution"])
def test_private_indexed_selection_cannot_replay_across_owner_boundaries(timeline_engine, changed_boundary):
    from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
    sentinel = "PRIVATE_SENTINEL_ONLY_ORIGINAL_READER"
    conversation_id = "household:fixture:conversation:private-read-" + changed_boundary
    # Private turns may use arbitrary IDs. Reusing household-shaped IDs lets the
    # audience case test the owner binding even when the indexed slot is identical.
    turn = replace(TURN, conversation_id=conversation_id, message_id=conversation_id + ":source")
    execution_id = "work:private-reader:" + changed_boundary
    adapter, request, fetches, _ = setup({"text": sentinel + "x" * 90000})
    authorized = []
    def route():
        return PostgresExternalConversationTools(timeline_engine,
            SimpleNamespace(action_names=("use_connector_tool",)),
            SimpleNamespace(supports_stored_results=True),
            authorize_turn=lambda candidate: authorized.append(candidate))
    with background_execution(BackgroundExecutionScope(turn, execution_id, lambda candidate: None)):
        timeline = PostgresOperationalTimeline(timeline_engine,
            external_timeline_scope(turn.tenant_id, turn.installation_id, "test"))
        capture = ConnectorResults(timeline, turn, lambda candidate: None)
        with result_capture(capture):
            reader = asyncio.run(adapter.execute(request)).details["invocation_response"]["reader"]["tool_id"]
        call = ConversationActionCall("private-read", "use_connector_tool", {
            "tool_id": reader, "arguments_json": json.dumps({"pointer": "/text", "offset": 0,
                                                               "limit": 100, "query": None})})
        original = route().execute(turn, call, step_index=4)
        assert sentinel in original.output["result"]["text"]
    other, other_execution = turn, execution_id
    if changed_boundary == "audience":
        other = replace(turn, audience=MemoryAudience.household(principal_id=turn.principal_id,
            household_id="fixture", consent_ref=HouseholdConsentReference(
                turn.principal_id, "fixture", "household-consent:fixture")))
    elif changed_boundary == "actor":
        other = replace(turn, principal_id="another-owner",
                        audience=MemoryAudience.private_person(principal_id="another-owner"))
    else:
        other_execution = execution_id + ":different"
    with background_execution(BackgroundExecutionScope(other, other_execution, lambda candidate: None)):
        candidate_route = route()
        if changed_boundary == "audience":
            with pytest.raises(ConversationConflict, match="replay differs") as pending_error:
                candidate_route.pending(other, step_index=4)
            assert sentinel not in str(pending_error.value)
        else:
            assert candidate_route.pending(other, step_index=4) is None
        with pytest.raises(ConversationConflict) as execute_error:
            candidate_route.execute(other, call, step_index=4)
        assert sentinel not in str(execute_error.value)
    with background_execution(BackgroundExecutionScope(turn, execution_id, lambda candidate: None)):
        assert route().pending(turn, step_index=4) == original
        assert route().execute(turn, call, step_index=4) == original
    assert other in authorized
    assert len(fetches) == 1
