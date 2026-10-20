"""Real durable connector receipts across program crashes; no provider traffic."""
from datetime import UTC, datetime
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from eva.adapters.postgres.tool_program_actions import NAME, ToolProgramActions
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationStopped, ConversationToolPreparation, ConversationTurn
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.providers.mcp_registration import _source


CONFIGURATION = "a" * 64
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
PROGRAM = """first=eva_tools.call('first_service',value=input_data['value'])
second=eva_tools.call('second_service',value=first['proof']['observed']['value']+1)
print(second['proof']['observed']['value'])
"""


class OfflineProcedure:
    """Only the synthetic local wrapper runs; this fake cannot call a service."""
    image_id = "sha256:" + "b" * 64

    def __init__(self, directory):
        self.directory, self.rounds = directory, 0

    def execute(self, source, input_data):
        self.rounds += 1
        path = self.directory / "program.py"
        path.write_text(source)
        raw = canonical_json_bytes(input_data)
        result = subprocess.run([sys.executable, "-I", "-B", str(path)],
            input=raw, capture_output=True, timeout=5)
        return {"contract": "EvaIsolatedProcedureExecution/v1", "image_id": self.image_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "result": {"exit_code": result.returncode, "timed_out": False, "cancelled": False,
                "stdout": result.stdout.decode(), "stderr": result.stderr.decode(),
                "stdout_truncated": False, "stderr_truncated": False}}


class Planner:
    action_names = ("first_service", "second_service")

    async def prepare(self, turn, call):
        assert set(call.arguments) == {"value"}
        return ConversationToolPreparation(call.name, call.arguments,
            {"value": call.arguments["value"]}, {}, tool_configuration_sha256=CONFIGURATION)


class Services:
    """Controlled service effects behind the actual durable adapter boundary."""
    def __init__(self, journal, turn, mode):
        self.journal, self.turn, self.mode = journal, turn, mode
        self.effects = []

    async def execute(self, request):
        self.effects.append((request.tool_name, request.operation_id))
        if self.mode == "unknown" and request.tool_name == "first_service":
            # An effect may have occurred, but its acknowledgement never arrived.
            # Absence of TOOL_EXECUTED is deliberately not treated as no effect.
            raise RuntimeError("synthetic effect acknowledgement lost")
        return ToolDispatchResult(True, request.operation_id, {"accepted": True})

    async def observe(self, request, dispatch):
        assert dispatch is not None and dispatch.accepted
        if self.mode == "stop" and request.tool_name == "first_service":
            self.journal.request_turn_stop(self.turn)
        return ToolProofObservation("verified", {"value": request.arguments["value"]},
            {"source": "controlled_service_independent_readback"})


def fixture(engine, directory, mode="normal"):
    turn = ConversationTurn.private("tool-program-pg", "home", "owner", "private-task",
        "program-" + mode, "Synthetic durable program task", "2026-10-20T12:00:00Z")
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "test scope")
    journal = PostgresConversationJournal(engine, scope)
    journal.admit(turn)
    timeline = PostgresOperationalTimeline(engine, scope)
    client = OfflineProcedure(directory)
    services = Services(journal, turn, mode)
    spec = SimpleNamespace(execution_id="work:program:" + mode,
        source=SimpleNamespace(model_dump=lambda **kwargs: _source(turn)),
        source_ingress_sha256=canonical_sha256(PostgresConversationJournal._ingress_data(turn)),
        grant=SimpleNamespace(maximum_tool_calls=31))
    state = {"dispatches": 0}

    def authorize(current):
        if current != turn:
            raise ConversationConflict("source differs")
        journal.ensure_running(turn)

    def dispatch(current):
        authorize(current)
        state["dispatches"] += 1

    def build():
        route = PostgresExternalConversationTools(engine, Planner(), services,
            authorize_turn=authorize, configuration_sha256=CONFIGURATION)
        adapter = ToolProgramActions(engine, spec, turn, authorize, dispatch, lambda: NOW,
            actions=lambda: actions, allowed_names=Planner.action_names, client=client)
        # Match the actual runner: the program adapter itself is also visited by
        # pending_step() when it probes the host-owned subcall slots.
        actions = BackgroundConnectorActions((route, adapter), (*Planner.action_names, NAME), authorize, 31)
        return adapter, actions, route

    call = ConversationActionCall("program-call", NAME,
        {"source": PROGRAM, "input_json": json.dumps({"value": 9})})
    return SimpleNamespace(turn=turn, spec=spec, timeline=timeline, client=client,
        services=services, state=state, dispatch=dispatch, build=build, call=call)


def test_crash_reopens_actual_external_receipt_without_repeating_effect(postgres_engine, tmp_path):
    f = fixture(postgres_engine, tmp_path)
    adapter, _, route = f.build()
    append = adapter._append

    def crash_after_effect(thread, event, data, **kwargs):
        if event == "PROGRAM_SUBCALL_RECORDED" and data["index"] == 0:
            raise RuntimeError("crash before program receipt record")
        return append(thread, event, data, **kwargs)

    adapter._append = crash_after_effect
    with background_execution(BackgroundExecutionScope(f.turn, f.spec.execution_id, f.dispatch)):
        with pytest.raises(RuntimeError, match="crash before program receipt"):
            adapter.execute(f.turn, f.call, step_index=3)
        first_step = adapter._substep(3, 0)
        first_thread = route._thread_id(f.turn, "first_service", first_step)
        before = f.timeline.events(first_thread)
        assert [event.event_type for event in before] == [
            "TOOL_REQUEST_ADMITTED", "TOOL_PREPARED", "TOOL_EXECUTED", "STATE_VERIFIED"]
        terminal_sha = before[-1].payload_sha256
        program_events = f.timeline.events(adapter._thread(3))
        assert program_events[-1].event_type == "PROGRAM_SUBCALL_REQUESTED"
        assert not any(e.event_type == "PROGRAM_SUBCALL_RECORDED" for e in program_events)
        assert len(f.services.effects) == 1

        # Reconstruct both the program adapter and its normal dispatcher. The
        # durable external receipt is recovered through actual pending_step().
        reopened, actions, reopened_route = f.build()
        recovered = actions.pending_step(f.turn, step_index=first_step)
        assert recovered.output["status"] == "verified"
        receipt_sha = canonical_sha256(recovered.output)
        result = reopened.execute(f.turn, f.call, step_index=3)
        assert result.output["program_result"]["output"].strip() == "10"
        assert [name for name, _ in f.services.effects] == ["first_service", "second_service"]
        after = f.timeline.events(first_thread)
        assert [e.payload_sha256 for e in after] == [e.payload_sha256 for e in before]
        assert after[-1].payload_sha256 == terminal_sha
        records = [e.payload["data"] for e in f.timeline.events(reopened._thread(3))
                   if e.event_type == "PROGRAM_SUBCALL_RECORDED"]
        assert records[0]["receipt_sha256"] == receipt_sha
        assert len(records) == 2

        rounds = f.client.rounds
        assert reopened.pending(f.turn, step_index=3) == result
        again, _, _ = f.build()
        assert again.execute(f.turn, f.call, step_index=3) == result
        assert f.client.rounds == rounds
        assert len(f.services.effects) == 2
        assert reopened_route.pending(f.turn, step_index=first_step) == recovered


def test_durable_stop_and_unknown_write_block_next_subcall_and_replay(postgres_engine, tmp_path):
    for mode in ("stop", "unknown"):
        f = fixture(postgres_engine, tmp_path, mode)
        adapter, _, route = f.build()
        expected = ConversationStopped if mode == "stop" else ConversationConflict
        with background_execution(BackgroundExecutionScope(f.turn, f.spec.execution_id, f.dispatch)):
            with pytest.raises(expected):
                adapter.execute(f.turn, f.call, step_index=4)
            assert [name for name, _ in f.services.effects] == ["first_service"]
            rounds = f.client.rounds
            first_thread = route._thread_id(f.turn, "first_service", adapter._substep(4, 0))
            first_events = f.timeline.events(first_thread)
            before_hashes = [e.payload_sha256 for e in first_events]
            if mode == "unknown":
                assert first_events[-1].event_type == "RECOVERY_FAILED"
                assert not any(e.event_type in {"TOOL_EXECUTED", "STATE_VERIFIED"} for e in first_events)
            with pytest.raises(expected):
                reopened, _, _ = f.build()
                reopened.execute(f.turn, f.call, step_index=4)
            assert f.client.rounds == rounds
            assert len(f.services.effects) == 1
            assert [e.payload_sha256 for e in f.timeline.events(first_thread)] == before_hashes
            second_thread = route._thread_id(f.turn, "second_service", adapter._substep(4, 1))
            assert f.timeline.events(second_thread) == ()
            assert not any(e.event_type == "PROGRAM_COMPLETED"
                           for e in f.timeline.events(adapter._thread(4)))
