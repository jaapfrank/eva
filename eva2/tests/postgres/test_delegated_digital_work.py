"""Real PostgreSQL digital lifecycle with controlled, verified connector effects."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from eva.operations.install_schema import install_schema
from pathlib import Path
from types import SimpleNamespace

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.application.conversation import (
    ConversationActionCall, ConversationModelResult, ConversationModelUsage,
    ConversationToolPreparation, ConversationTurn, GroundedReply, IdentityProfile,
)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_autonomy import _SourceTurnLoader
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_delegated_delivery_store import NOW


@pytest.fixture
def delegated_engine():
    """Use the current source schema in an explicitly disposable database."""
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if not url.database or not url.database.startswith("eva2_4b_disposable_"):
        pytest.fail("digital work requires an explicit disposable database")
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(sa.text("SHOW server_version_num"))) == 160013
            assert not connection.scalar(sa.text(
                "SELECT to_regclass('public.eva_alembic_version') IS NOT NULL"))
        receipt = install_schema(dsn, alembic_ini=Path(__file__).resolve().parents[2] / "alembic.ini")
        assert receipt.final_revision == "4e_0009_restore_hold"
        yield engine
    finally:
        engine.dispose()


def test_digital_stop_resume_reuses_verified_effect_and_returns_once(delegated_engine):
    label = "digital-pg-lifecycle"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    now = [NOW]
    root = Path(__file__).resolve().parents[2] / "src/eva"
    name = "bootstrap/delegated_digital_runner.py"
    profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((name, hashlib.sha256((root / name).read_bytes()).hexdigest()),))
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
        principal_binding_id=binding, clock=lambda: now[0], digital_profile=profile)
    turn = ConversationTurn.private("tenant:" + label, "installation:" + label, str(owner),
        "dashboard:" + str(binding), "digital:source", "Compare the documents",
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    journal = PostgresConversationJournal(delegated_engine, scope)
    journal.admit(turn)
    foreground = GroundedReply(turn.conversation_id, turn.message_id,
        "Ik ga ermee aan de slag.", (), turn.occurred_at, "controlled-contact")
    journal.commit_reply(turn, foreground)
    admitted = service.execute("digital", turn, task=turn.text, request_key="digital:first")
    first = store.load(admitted["work"]["work_id"], **service._owner).spec
    assert first.source_ingress_sha256 == canonical_sha256(journal._ingress_data(turn))
    assert service.private_return(turn, "poll")["result"] is None
    effects = {}
    dispatches = []

    class Memory:
        def recall_for_presence(self, query):
            return HindsightMemoryEngine._empty_presence(query)

    class Planner:
        action_names = profile.capability_ids

        async def prepare(self, source, call):
            assert source == turn
            return ConversationToolPreparation("fixture.document", call.arguments,
                {"done": True}, {"document": call.arguments["document"]})

    class Provider:
        async def execute(self, request):
            assert request.operation_id not in effects, "verified effect was repeated"
            dispatches.append(request.arguments["document"])
            effects[request.operation_id] = {"done": True}
            return ToolDispatchResult(True, request.operation_id, {})

        async def observe(self, request, dispatch):
            return ToolProofObservation("verified", effects[request.operation_id], {})

    class Model:
        stop_after_first = True

        def generate(self, source, identity, memory, recent_messages, *, completed_action=None, **kwargs):
            assert source == turn
            if completed_action is None:
                name, document = "discover_connector_tools", "first"
            elif completed_action.call.name == "discover_connector_tools":
                assert completed_action.output["status"] == "verified"
                if self.stop_after_first:
                    self.stop_after_first = False
                    service.execute("stop", turn, work_id=first.work_id, request_key="stop:first")
                name, document = "use_connector_tool", "second"
            else:
                assert completed_action.output["status"] == "verified"
                name = document = None
            return ConversationModelResult(None if name else "The comparison was saved and checked.",
                "controlled", None if name is None else ConversationActionCall(
                    "call:" + document, name, {"document": document}),
                usage=ConversationModelUsage.measured(tool_calls=int(name is not None),
                    input_tokens=30, output_tokens=10))

    authority = SimpleNamespace(scope=scope, authorize_turn=service.validate_authority)
    runner = DelegatedDigitalRunner(store, profile,
        source_loader=_SourceTurnLoader(delegated_engine, authority), memory=Memory(), model=Model(),
        identity=IdentityProfile("Eva", "Helpful", ("Be honest",)),
        connector_tools=(PostgresExternalConversationTools(delegated_engine, Planner(), Provider(),
                         authorize_turn=service.validate_authority),), clock=lambda: now[0])
    now[0] += timedelta(seconds=1)
    claim = store.claim_next(worker_id="worker:digital-test", release=profile.release, now=now[0])
    assert claim is not None and claim.spec == first
    now[0] += timedelta(seconds=1)
    call = store.start_call(claim, now=now[0])
    outcome = asyncio.run(runner.run(first, call_id=call.call_id))
    assert outcome.result is None and dispatches == ["first"]
    assert outcome.usage.tool_calls == 1
    store.record_stopped(call, usage=outcome.usage, now=now[0])
    assert journal.committed_reply(turn) == foreground

    now[0] += timedelta(seconds=1)
    resume_turn = replace(turn, message_id="digital:resume", text="Continue the task",
                          occurred_at=now[0].strftime("%Y-%m-%dT%H:%M:%SZ"))
    resumed = service.execute("resume", resume_turn, work_id=first.work_id, request_key="resume:first")
    second = store.load(resumed["work"]["work_id"], **service._owner).spec
    assert second.work_id != first.work_id and second.execution_id == first.execution_id
    assert second.source == first.source and second.source_ingress_sha256 == first.source_ingress_sha256
    now[0] += timedelta(seconds=1)
    claim = store.claim_next(worker_id="worker:digital-test", release=profile.release, now=now[0])
    assert claim is not None and claim.spec == second
    call = store.start_call(claim, now=now[0])
    outcome = asyncio.run(runner.run(second, call_id=call.call_id))
    assert outcome.status == "succeeded", outcome
    assert dispatches == ["first", "second"]
    assert outcome.usage.tool_calls == 1 and outcome.usage.completed_model_responses == 2
    assert outcome.usage.cost_usd_micros is None
    store.record_success(call, outcome.result, now=now[0])
    loaded = store.load(second.work_id, **service._owner)
    assert loaded.result == outcome.result and loaded.result.contract == "DelegatedDigitalWorkResult/v4"
    returned = service.private_return(turn, "poll")["result"]
    assert returned["work_id"] == second.work_id and returned["output"] == outcome.result.output
    identity = {key: returned[key] for key in ("work_id", "operation_id")}
    assert service.private_return(turn, "ack", **identity)["status"] == "acknowledged"
    assert service.private_return(turn, "ack", **identity)["status"] == "acknowledged"
    assert service.private_return(turn, "poll")["result"] is None
    assert journal.committed_reply(turn) == foreground
