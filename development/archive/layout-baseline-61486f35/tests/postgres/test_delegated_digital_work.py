"""Real PostgreSQL digital lifecycle with controlled, verified connector effects."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from alembic.config import Config
from alembic.script import ScriptDirectory
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
        expected_head = ScriptDirectory.from_config(Config(str(
            Path(__file__).resolve().parents[2] / "alembic.ini"))).get_current_head()
        assert receipt.final_revision == expected_head
        yield engine
    finally:
        engine.dispose()


def test_digital_stop_resume_reuses_verified_effect_and_returns_once(delegated_engine, monkeypatch):
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
    from eva.bootstrap.delegated_work_memory import DelegatedWorkMemory
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    outbox = PostgresMemoryOutbox(delegated_engine, tenant_id=turn.tenant_id,
                                 installation_id=turn.installation_id)
    submitted = []
    def submit(document):
        submitted.append(document)
        outbox.enqueue(document)
    memory_projection = DelegatedWorkMemory(delegated_engine, service, submit, clock=lambda: now[0])
    assert memory_projection.run_once() == "idle"  # activates; historical stopped attempt excluded

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


    # The real recorded outcome reaches the same durable person memory outbox once.
    memory_projection._next_poll = 0.0
    assert memory_projection.run_once() == "queued"
    assert len(submitted) == 1
    doc = submitted[0]
    assert doc.principal_id == turn.principal_id and outcome.result.output in doc.content
    assert doc.source_ref == turn.message_id and doc.trust == "model_inference_unconfirmed"
    assert len(outbox.pending()) == 1
    restarted = DelegatedWorkMemory(delegated_engine, service, submit, clock=lambda: now[0])
    assert restarted.run_once() == "idle"
    assert len(submitted) == 1  # no new extraction or resurrected historical attempt
    assert store.next_memory_outcome(**service._owner, now=now[0] + timedelta(days=60),
                                    completed_since=NOW) is None


    # Actual scoped source lookup and the existing owner command erase only this document.
    from eva.application.memory import MemoryRetentionReceipt
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    receipt = MemoryRetentionReceipt(bank_id="controlled:person", document_id=doc.document_id,
        content_sha256=hashlib.sha256(doc.content.encode()).hexdigest())
    job = outbox.begin(outbox.pending()[0], now=now[0])
    outbox.succeed(job, receipt, now=now[0])
    reader = PostgresOwnerMemorySources(delegated_engine, tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, owner=owner, binding=binding,
        authority_scope=scope)
    page = reader.page(now=now[0])
    source_id = page.sources[0].source_id
    assert reader.document(source_id, now=now[0])[0] == doc
    assert memory_projection.owns(doc)
    assert not memory_projection.owns(replace(doc, content=doc.content + " forged"))
    erased_documents = []
    def erase_document(**kwargs):
        assert kwargs["source_message_id"] is None
        erased_documents.append(kwargs["document_id"])
        outbox.erase_content(principal_sha256=hashlib.sha256(kwargs["principal_id"].encode()).hexdigest(),
            document_id=kwargs["document_id"], operation_sha256="ab" * 32,
            erased_at=kwargs["now"])
        return True
    owner_runtime = object.__new__(PostgresHindsightMemoryRuntime)
    owner_runtime._owner_memory_sources = lambda: reader
    owner_runtime._work_memory = memory_projection
    owner_runtime._cold_path = SimpleNamespace(erase_document=erase_document)
    monkeypatch.setattr("eva.bootstrap.runtime.datetime",
        SimpleNamespace(now=lambda tz=None: now[0]))
    assert owner_runtime.dashboard_forget_memory_source(source_id)["state"] == "erased"
    assert erased_documents == [doc.document_id]
    with pytest.raises(LookupError):
        reader.document(source_id, now=now[0] + timedelta(days=1))
    assert journal.committed_reply(turn) == foreground  # original request/reply preserved
    restarted = DelegatedWorkMemory(delegated_engine, service, submit, clock=lambda: now[0])
    assert restarted.run_once() == "idle" and len(submitted) == 1
