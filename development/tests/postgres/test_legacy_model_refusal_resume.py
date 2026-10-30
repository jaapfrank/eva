"""Real durable owner resume; no Google/network/model traffic or product success."""
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace

from eva.adapters.postgres import conversation_journal as journal_module
from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.programming_actions import ProgrammingActions
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.adapters.postgres.work_corrections import reconcile_legacy_model_refusal
from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationModelUsage
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from tests.postgres.test_delegated_digital_work import delegated_engine
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_delegated_delivery_store import NOW
from tests.operations.test_programming_service import service as programming_host_service
from eva.adapters.connectors.programming_workspace import ProgrammingWorkspaceClient


def test_only_sealed_local_model_refusal_can_continue_once(delegated_engine, monkeypatch, tmp_path, programming_host_service):
    now = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now[0]
    monkeypatch.setattr(journal_module, "datetime", Clock)
    server, host_workspace = programming_host_service
    token_file = tmp_path / "host-token"
    token_file.write_text("x" * 40)
    host_client = ProgrammingWorkspaceClient("http://127.0.0.1:" + str(server.server_address[1]), str(token_file))
    outcomes = []
    for case in ("complete", "unclosed_model", "missing_model_result", "missing_tool_receipt",
                 "external_dispatch", "wrong_source", "stopped", "cleanup_unconfirmed", "cleanup_non_boolean", "other_error"):
        label = "legacy-refusal-" + case
        scope, _, binding = _seed_authority(delegated_engine, label)
        owner = _id(label, "principal")
        store = PostgresDelegatedWorkStore(delegated_engine, scope)
        source = "bootstrap/delegated_digital_runner.py"
        root = Path(__file__).resolve().parents[3] / "src/eva"
        profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
            ("write_programming_file",), ((source, hashlib.sha256((root/source).read_bytes()).hexdigest()),))
        service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
            principal_binding_id=binding, clock=lambda: now[0], digital_profile=profile)
        turn = ConversationTurn.private("tenant:" + label, "installation:" + label, str(owner),
            "dashboard:" + str(binding), "source:" + case, "Make a local artifact",
            NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        now[0] = NOW
        journal = PostgresConversationJournal(delegated_engine, scope)
        journal.admit(turn)
        admitted = service.execute("digital", turn, task=turn.text, request_key="first")
        spec = store.load(admitted["work"]["work_id"], **service._owner).spec
        now[0] += timedelta(seconds=1)
        claim = store.claim_next(worker_id="worker:" + case, release=profile.release, now=now[0])
        call = store.start_call(claim, now=now[0])
        accounting = BackgroundModelJournal(journal, turn, work_id=spec.work_id)
        request = accounting.begin_model_request(turn)
        target = tmp_path / (case + ".txt")
        closed = []
        class Client:
            def request(self, *args, **kwargs):
                target.write_text("existing result")
                return {"isError": False, "content": [{"type": "text", "text": "written"}]}
            def close(self, key):
                closed.append(key)
                if case == "cleanup_unconfirmed": return {"closed": False}
                if case == "cleanup_non_boolean": return {"closed": 1}
                return host_client.close(key)
        client = Client()
        now[0] += timedelta(seconds=1)
        if case not in {"missing_tool_receipt", "external_dispatch"}:
            actions = ProgrammingActions(delegated_engine, spec, turn, service.validate_authority,
                lambda turn: None, lambda: now[0], client=client)
            actions.execute(turn, ConversationActionCall("write", "write_programming_file",
                {"path": "/workspace/result.txt", "content": "existing result", "mode": "rewrite"}))
        accounting.complete_model_request(turn, request, ConversationModelUsage.measured(
            input_tokens=10, output_tokens=10, tool_calls=1), local_stream_closed=True)
        now[0] += timedelta(seconds=1)
        last = accounting.begin_model_request(turn)
        if case != "missing_model_result":
            accounting.complete_model_request(turn, last, ConversationModelUsage.unmeasured(),
                outcome="failed", local_stream_closed=None if case == "unclosed_model" else True)
        now[0] += timedelta(seconds=1)
        work = store.record_failure(call, usage=DelegatedDigitalRunner._usage(accounting.model_usage(turn), 1),
            error_code="internal_error", error_fingerprint=("a" * 64 if case == "other_error" else
                hashlib.sha256(b"ConversationModelRateLimited:conversation provider rate limited").hexdigest()),
            ambiguous=True, now=now[0])
        examined = replace(work, stop_requested_at=now[0]) if case == "stopped" else work
        source_turn = replace(turn, text="different source") if case == "wrong_source" else turn
        before = work
        proof = reconcile_legacy_model_refusal(delegated_engine, examined, source_turn,
            now=now[0], close_session=client.close)
        assert store.load(spec.work_id, **service._owner) == before
        if case != "complete":
            assert proof is None, case
            outcomes.append(case)
            continue
        assert proof["external_effects_reconciled"] is False and len(proof["local_heads"]) == 1
        assert target.read_text() == "existing result" and len(closed) == 1
        monkeypatch.setattr("eva.adapters.connectors.programming_workspace.configured_programming_workspace", lambda: client)
        # Use the actual delayed-owner path: an old ingress must not give the
        # never-started successor an already-expired execution clock.
        now[0] += timedelta(seconds=profile.timeout_seconds + 1)
        scheduler = ScheduledOwnerWork(service)
        planned = scheduler.schedule(turn, request_key="resume", request={"work_id": spec.work_id,
            "run_at": (now[0] + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}, resume=True)
        now[0] += timedelta(seconds=2)
        assert scheduler.run_once() == "dispatched"
        scheduled = next(item for item in scheduler.list()["schedules"]
                         if item["schedule_id"] == planned["schedule_id"])
        successor = store.load(scheduled["work_id"], **service._owner)
        assert successor.created_at == now[0]
        assert successor.spec.deadline > successor.spec.created_at
        assert successor.call_id is None
        assert successor.spec.source == spec.source and successor.spec.source_ingress_sha256 == spec.source_ingress_sha256
        assert successor.spec.execution_id == spec.execution_id and successor.spec.grant == spec.grant
        replay = service.execute("resume", turn, work_id=spec.work_id, request_key="resume-again")
        assert replay["work"]["work_id"] == successor.spec.work_id
        assert store.load(spec.work_id, **service._owner) == before and target.read_text() == "existing result"
        # Leave no active synthetic work for the next isolated case.
        store.request_stop(successor.spec.work_id, **service._owner, now=now[0])
        outcomes.append(case)
    assert len(outcomes) == 10
