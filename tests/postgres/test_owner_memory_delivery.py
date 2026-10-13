"""Real owner HTTP → durable journal/outbox → retention → raw/derived erasure.

The semantic provider is a deterministic test double; live Hindsight proof is separate.
"""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import time
from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.runtime_resource_usage import PostgresMemoryStorageBudget
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_background import HindsightColdPathWorker
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from eva.operations.portable_restore_authority import PrivateRestoreAuthorityStore
from tests.postgres.test_people_memory_overview import people_memory_engine
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_runtime_resource_usage import _limited_storage_budget


def test_owner_http_correct_and_forget_real_sources(people_memory_engine, private_tmp_path, monkeypatch):
    import eva.bootstrap.runtime as module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(module, "datetime", Clock)
    label = "memory-controls-http"
    tenant, install = f"tenant:{label}", f"installation:{label}"
    scope, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    budget = replace(_limited_storage_budget(install), tenant_id=tenant)
    storage = PostgresMemoryStorageBudget(people_memory_engine, tenant_id=tenant,
        installation_id=install, resource_budget=budget)
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=install)
    journal = PostgresConversationJournal(people_memory_engine, scope)
    documents = {}
    class Memory:
        def retain_conversation(self, doc):
            documents[doc.document_id] = doc
            return MemoryRetentionReceipt("eva2-" + "a"*32, doc.document_id,
                hashlib.sha256(doc.content.encode()).hexdigest())
        def remove_conversation_document(self, *, bank_id, document_id, principal_sha256):
            documents.pop(document_id, None)
    private_tmp_path.chmod(0o700)
    authority = PrivateRestoreAuthorityStore(private_tmp_path / "restore-authority.json",
        source_installation_id="22222222-2222-4222-8222-222222222222")
    worker = HindsightColdPathWorker(Memory(), outbox=outbox, storage_budget=storage,
        conversation_erasure=journal, restore_authority_erasure=authority, idle_seconds=3600)
    obj = object.__new__(PostgresHindsightMemoryRuntime)
    obj._engine = people_memory_engine; obj._runtime_tenant_id = tenant; obj._runtime_installation_id = install
    obj._dashboard_owner_principal_id = owner; obj._dashboard_principal_binding_id = binding
    obj._dashboard_authority_scope = scope; obj._conversation_actions = None; obj._cold_path = worker
    obj._telegram_principal_bindings_loader = None
    obj._telegram_principal_bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),))
    client = TestClient(create_app(obj, dashboard_access_token="t"*32))
    headers = {"authorization": "Bearer " + "t"*32}
    base = "/internal/v1/dashboard/memory-sources"
    turn = ConversationTurn.private(tenant_id=tenant, installation_id=install, principal_id="telegram:101",
        conversation_id="telegram:101", message_id="telegram:101:999", text="Mijn testkleur is blauw.",
        occurred_at="2026-08-29T11:59:00Z")
    reply = GroundedReply(turn.conversation_id, turn.message_id, "Ontvangen.", (), turn.occurred_at, "test")
    journal.admit(turn); journal.commit_reply(turn, reply)
    worker.start()
    try:
        obj.after_response(turn, reply)
        def sources(count):
            end = time.monotonic() + 10
            while time.monotonic() < end:
                response = client.get(base, headers=headers)
                assert response.status_code == 200, response.text
                rows = response.json()["sources"]
                if len(rows) == count and all(x["retention_state"] == "retained" for x in rows): return rows
                time.sleep(.05)
            raise AssertionError("retention did not finish")
        original = sources(1)[0]
        selected = base + "/" + original["source_id"]
        assert "blauw" in client.get(selected, headers=headers).json()["content"]
        response = client.post(selected + "/correct", headers=headers, json={"text": "Mijn testkleur is nu groen."})
        assert response.status_code == 202, response.text
        rows = sources(2)
        corrected = next(x for x in rows if "groen" in x["excerpt"])
        assert len(documents) == 2
        for source in [original, corrected]:
            url = base + "/" + source["source_id"]
            response = client.post(url + "/forget", headers=headers, json={"confirm": True})
            assert response.status_code == 200 and response.json()["state"] == "erased", response.text
            assert client.get(url, headers=headers).status_code == 404
        assert sources(0) == [] and documents == {}
        assert journal.recent(turn) == ()
        assert outbox.pending() == ()
    finally:
        worker.stop()
