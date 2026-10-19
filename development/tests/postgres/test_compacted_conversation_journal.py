"""Synthetic consumer boundaries; scoped SQL compaction primitive is tested separately."""
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import CheckpointConflict, TimelineScope
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.application.memory_source_lifecycle import MemorySourceCompactionProof
from eva.contracts.canonical_json import canonical_sha256

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
SCOPE = TimelineScope(UUID("66a4aad5-0550-5211-8b36-398eabe5b7be"),
                      UUID("0a44a85c-4bbb-5330-8803-60fab86e5a94"))


def proof():
    fields = {key: "a" * 64 for key in (
        "queued_payload_sha256", "content_sha256", "source_payload_sha256",
        "ingress_payload_sha256", "ingress_text_sha256", "reply_payload_sha256",
        "reply_text_sha256", "cohort_manifest_sha256", "receipt_sha256",
        "source_identity_sha256")}
    return MemorySourceCompactionProof(contract="EvaPrivateMemorySourceCompaction/v1",
        compacted_at="2026-10-19T10:00:00Z", bank_id="synthetic-bank",
        operation_id="synthetic-operation", selected_fact_count=1,
        disposition="retained", **fields).to_wire()


def turn():
    return ConversationTurn.private("tenant:synthetic", "installation:synthetic",
        "person:synthetic", "chat:synthetic", "message:synthetic",
        "Original synthetic text", "2026-10-18T10:00:00Z")


def row(kind, data, sequence=1):
    payload = {"data": data}
    return SimpleNamespace(event_id=UUID(int=sequence), event_type=kind,
        occurred_at=NOW, sequence=sequence, payload=payload,
        payload_sha256=bytes.fromhex(canonical_sha256(payload)))


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.updated = []
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def begin(self):
        return SimpleNamespace(rollback=lambda: None)

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self.rows)

    def scalar(self, statement, params):
        import json
        self.updated.append(params)
        target = next(r for r in self.rows if r.event_id == params["event_id"])
        assert target.payload_sha256 == params["expected_hash"]
        target.payload = json.loads(params["payload"])
        target.payload_sha256 = params["new_hash"]
        assert bytes.fromhex(canonical_sha256(target.payload)) == target.payload_sha256
        return True


def journal(monkeypatch, rows=()):
    connection = Connection(list(rows))
    engine = SimpleNamespace(url=SimpleNamespace(get_backend_name=lambda: "postgresql"),
        connect=lambda: connection, begin=lambda: connection)
    instance = PostgresConversationJournal(engine, SCOPE)
    monkeypatch.setattr(instance, "_scope", lambda _: None)
    return instance, connection


def compacted_data(j, kind):
    t = turn()
    if kind == "INGRESS_COMMITTED":
        data = j._ingress_data(t)
    else:
        data = {"kind": "conversation_reply", "conversation_id": t.conversation_id,
            "message_id": t.message_id, "principal_id": t.principal_id,
            "provider": "synthetic", "evidence_memory_ids": []}
    original_ingress = {"data": j._ingress_data(t)}
    data.pop("text", None)
    data["content_compaction"] = proof()
    if kind == "INGRESS_COMMITTED":
        data["content_compaction"]["ingress_payload_sha256"] = canonical_sha256(original_ingress)
    else:
        data["text_sha256"] = data["content_compaction"]["reply_text_sha256"]
    return data


def test_compacted_ingress_replay_is_explicitly_expired_and_never_loaded(monkeypatch):
    j, _ = journal(monkeypatch)
    data = compacted_data(j, "INGRESS_COMMITTED")
    durable = row("INGRESS_COMMITTED", data)
    durable.occurred_at = j._timestamp(turn().occurred_at)
    class Timeline:
        def create_thread(self, **kwargs):
            raise CheckpointConflict("already exists")
        def events(self, _):
            return (durable,)
        def load(self, _):
            pytest.fail("compacted ingress must never resume")
    j._timeline = Timeline()
    original = canonical_sha256(durable.payload)
    with pytest.raises(ConversationConflict, match="day text has expired"):
        j.admit(turn())
    assert canonical_sha256(durable.payload) == original


@pytest.mark.parametrize("erased", [False, True])
def test_committed_reply_distinguishes_compaction_from_true_erasure(monkeypatch, erased):
    j, _ = journal(monkeypatch)
    data = compacted_data(j, "REPLY_COMMITTED")
    if erased:
        data["content_erasure"] = {"status": "erased"}
    j._timeline = SimpleNamespace(events=lambda _: (row("REPLY_COMMITTED", data),))
    with pytest.raises(ConversationConflict, match="was erased" if erased else "day text has expired"):
        j.committed_reply(turn())


@pytest.mark.parametrize("change", ["bad-proof", "raw-coexists", "reply-hash"])
def test_compacted_reply_cannot_become_synthetic_source_text(monkeypatch, change):
    j, _ = journal(monkeypatch)
    data = compacted_data(j, "REPLY_COMMITTED")
    if change == "bad-proof":
        data["content_compaction"]["content_sha256"] = "invalid"
    elif change == "raw-coexists":
        data["text"] = "This metadata placeholder is not original testimony"
    else:
        data["text_sha256"] = "f" * 64
    j._timeline = SimpleNamespace(events=lambda _: (row("REPLY_COMMITTED", data),))
    with pytest.raises(ConversationConflict, match="compaction proof|reply hash"):
        j.committed_reply(turn())


def test_recent_skips_compacted_or_erased_rows_even_if_text_is_present(monkeypatch):
    t = turn()
    values = [row("REPLY_COMMITTED", {"text": "metadata placeholder", "content_compaction": {}}),
        row("INGRESS_COMMITTED", {"text": "erased placeholder", "content_erasure": {}}),
        row("INGRESS_COMMITTED", {"text": "Visible synthetic text", "message_id": "other"})]
    j, connection = journal(monkeypatch, values)
    assert [m.text for m in j.recent(t)] == ["Visible synthetic text"]
    sql = str(connection.statements[0])
    assert "NOT" in sql


def test_compacted_exchange_can_be_truly_erased_and_replayed_without_losing_proof(monkeypatch):
    j, _ = journal(monkeypatch)
    rows = [row(kind, compacted_data(j, kind), index)
        for index, kind in enumerate(("INGRESS_COMMITTED", "REPLY_COMMITTED"), 1)]
    j, connection = journal(monkeypatch, rows)
    original_proofs = [r.payload["data"]["content_compaction"].copy() for r in rows]
    receipt = j.erase_message_content(principal_id=turn().principal_id,
        message_id=turn().message_id, operation_sha256="b" * 64, erased_at=NOW)
    assert receipt.redacted_event_count == 2 and not receipt.replayed
    assert len(connection.updated) == 2
    for r, original in zip(rows, original_proofs):
        assert r.payload["data"]["content_compaction"] == original
        assert r.payload["data"]["content_erasure"]["status"] == "erased"
        assert "text" not in r.payload["data"] and "text_sha256" not in r.payload["data"]
    replay = j.erase_message_content(principal_id=turn().principal_id,
        message_id=turn().message_id, operation_sha256="b" * 64, erased_at=NOW)
    assert replay.replayed and replay.redacted_event_count == 0
    assert len(connection.updated) == 2


@pytest.mark.parametrize("damage", ["proof", "payload-hash", "other-principal", "missing-proof"])
def test_compacted_erasure_rejects_invalid_or_wrong_scope_before_cas(monkeypatch, damage):
    j, _ = journal(monkeypatch)
    data = compacted_data(j, "INGRESS_COMMITTED")
    if damage == "proof":
        data["content_compaction"]["operation_id"] = ""
    elif damage == "other-principal":
        data["principal_id"] = "person:other"
    elif damage == "missing-proof":
        data.pop("content_compaction")
    candidate = row("INGRESS_COMMITTED", data)
    if damage == "payload-hash":
        candidate.payload_sha256 = bytes(32)
    j, connection = journal(monkeypatch, [candidate])
    with pytest.raises(ConversationConflict):
        j.erase_message_content(principal_id=turn().principal_id,
            message_id=turn().message_id, operation_sha256="b" * 64, erased_at=NOW)
    assert connection.updated == []


@pytest.mark.parametrize("change", ["text", "principal", "timestamp"])
def test_expired_ingress_still_rejects_reused_message_identity(monkeypatch, change):
    j, _ = journal(monkeypatch)
    data = compacted_data(j, "INGRESS_COMMITTED")
    durable = row("INGRESS_COMMITTED", data)
    durable.occurred_at = j._timestamp(turn().occurred_at)
    def duplicate(**kwargs):
        raise CheckpointConflict("already exists")
    j._timeline = SimpleNamespace(create_thread=duplicate, events=lambda _: (durable,))
    changes = {"text": {"text": "Different synthetic input"},
        "principal": {"principal_id": "person:other", "audience": None},
        "timestamp": {"occurred_at": "2026-10-18T10:01:00Z"}}[change]
    if change == "principal":
        from eva.application.memory_audience import MemoryAudience
        changes["audience"] = MemoryAudience.private_person(principal_id="person:other")
    with pytest.raises(ConversationConflict, match="identity was reused"):
        j.admit(replace(turn(), **changes))
