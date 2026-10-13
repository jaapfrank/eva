from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from eva.application.conversation import ConversationConflict
from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience

SOURCE = str(uuid4())

def runtime(state="retained"):
    doc = ConversationMemoryDocument(tenant_id="tenant", installation_id="install",
        principal_id="telegram:101", conversation_id="telegram:101",
        document_id="telegram:101:exchange:telegram:101:44", content="Mijn favoriete kleur is blauw.",
        occurred_at="2026-09-11T10:00:00Z", source_ref="telegram:101:44", evidence_ref="conversation-reply:telegram:101:44",
        privacy_class="internal", trust="direct_user", audience=MemoryAudience.private_person(principal_id="telegram:101"))
    obj = object.__new__(PostgresHindsightMemoryRuntime)
    reads = []; erasures = []; retained = []; journal = []
    def read(source_id, **kw):
        reads.append(source_id)
        if source_id != SOURCE: raise LookupError()
        return doc, state
    obj._owner_memory_sources = lambda: SimpleNamespace(document=read)
    obj._cold_path = SimpleNamespace(erase_document=lambda **kw: erasures.append(kw) or True)
    obj._journal = lambda turn: SimpleNamespace(admit=lambda turn: journal.append(turn), commit_reply=lambda turn, reply: reply, load=lambda turn: SimpleNamespace(phase="memory_queued"))
    obj.after_response = lambda turn, reply: retained.append((turn, reply))
    return obj, reads, erasures, retained, journal

def test_exact_source_erasure_uses_server_owned_alias_and_raw_binding():
    obj, reads, erasures, _, _ = runtime()
    assert obj.dashboard_forget_memory_source(SOURCE)["state"] == "erased"
    assert reads == [SOURCE]
    assert erasures[0]["principal_id"] == "telegram:101"
    assert erasures[0]["source_message_id"] == "telegram:101:44"
    assert erasures[0]["document_id"] == "telegram:101:exchange:telegram:101:44"
    with pytest.raises(LookupError): obj.dashboard_forget_memory_source(str(uuid4()))
    assert len(erasures) == 1

def test_unretained_source_cannot_be_falsely_reported_erased():
    obj, _, erasures, _, _ = runtime("pending")
    with pytest.raises(ConversationConflict): obj.dashboard_forget_memory_source(SOURCE)
    assert erasures == []

def test_correction_reuses_durable_conversation_and_current_source_owner():
    obj, reads, erasures, retained, journal = runtime()
    result = obj.dashboard_correct_memory_source(SOURCE, "Mijn favoriete kleur is groen.")
    assert result["state"] == "correction_queued" and not erasures
    turn, reply = retained[0]
    assert journal == [turn] and reads == [SOURCE]
    assert turn.principal_id == turn.conversation_id == "telegram:101"
    assert turn.audience.principal_id == "telegram:101"
    assert "groen" in turn.text and "blauw" not in turn.text
    assert turn.message_id.startswith("dashboard-memory:")
    assert reply.provider == "owner-memory-controls"
    with pytest.raises(LookupError): obj.dashboard_correct_memory_source(str(uuid4()), "other")
    assert len(retained) == 1


def test_rejected_retention_is_not_reported_as_queued():
    obj, _, _, _, _ = runtime()
    obj._journal = lambda turn: SimpleNamespace(admit=lambda turn: None, commit_reply=lambda turn, reply: reply,
        load=lambda turn: SimpleNamespace(phase="memory_skipped"))
    with pytest.raises(ConversationConflict, match="geblokkeerd"):
        obj.dashboard_correct_memory_source(SOURCE, "Mijn favoriete kleur is groen.")
