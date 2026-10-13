"""Private retained history can find terms introduced only by Eva's reply."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.private_continuity import _exchange_content
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_scheduled_failure_return import database


NOW = datetime(2026, 8, 26, 12, 10, tzinfo=UTC)


def test_reply_only_match_respects_exact_binding_erasure_retention_and_private_scope(database):
    label = "private-history-reply"
    scope, _, binding = _seed_authority(database, label)
    owner = _id(label, "principal")
    tenant, installation = "tenant:" + label, "installation:" + label
    dashboard = "dashboard:" + str(binding)
    aliases = TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),))
    reader = PostgresOwnerMemorySources(database, tenant_id=tenant, installation_id=installation,
        owner=owner, binding=binding, authority_scope=scope, telegram_bindings=aliases)
    outbox = PostgresMemoryOutbox(database, tenant_id=tenant, installation_id=installation)
    journal = PostgresConversationJournal(database, scope)
    caller = ConversationTurn.private(tenant, installation, str(owner), dashboard,
        "history-request", "What did you recommend?", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def exchange(name, *, principal=None, conversation=None, reply_changes=None,
                 erased_source=False, erased_reply=False, expired=False, future=False,
                 age=timedelta(minutes=5)):
        principal = principal or str(owner)
        at = NOW - age
        turn = ConversationTurn.private(tenant, installation, principal, conversation or dashboard,
            "history-source:" + name, "Which option would you recommend?", at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        doc = ConversationMemoryDocument(tenant_id=tenant, installation_id=installation,
            principal_id=principal, conversation_id=turn.conversation_id, document_id="history-doc:" + name,
            content=turn.text, occurred_at=turn.occurred_at, source_ref=turn.message_id,
            evidence_ref="conversation-reply:" + turn.message_id, privacy_class="internal",
            trust="direct_user", audience=turn.audience,
            retention_until=(NOW - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if expired else None)
        outbox.enqueue(doc)
        changes = dict(reply_changes or {})
        if "principal_id" in changes:
            changes["audience"] = MemoryAudience.private_person(principal_id=changes["principal_id"])
        reply_turn = replace(turn, **changes)
        reply = GroundedReply(reply_turn.conversation_id, reply_turn.message_id,
            "Consider a warmtepomp.", (),
            (NOW + timedelta(seconds=1) if future else at + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "fixture")
        journal.admit(reply_turn)
        journal.commit_reply(reply_turn, reply)
        if erased_reply:
            journal.erase_message_content(principal_id=reply_turn.principal_id, message_id=reply_turn.message_id,
                operation_sha256="a" * 64, erased_at=NOW)
        if erased_source:
            import hashlib
            outbox.erase_content(principal_sha256=hashlib.sha256(principal.encode()).hexdigest(),
                document_id=doc.document_id, operation_sha256="b" * 64, erased_at=NOW)
        return doc

    matching = exchange("valid", principal="telegram:101", conversation="telegram:101",
        age=timedelta(days=8))
    exchange("wrong-person", reply_changes={"principal_id": "other-person"})
    exchange("wrong-conversation", reply_changes={"conversation_id": "other-conversation"})
    exchange("wrong-message", reply_changes={"message_id": "other-message"})
    exchange("wrong-project", reply_changes={"project_id": "other-project"})
    exchange("erased-reply", erased_reply=True)
    exchange("erased-source", erased_source=True)
    exchange("expired", expired=True)
    exchange("future", future=True)
    exchange("other-source", principal="other-person", conversation="telegram:909")
    # A newer retained source without the searched word must not consume LIMIT 1.
    outbox.enqueue(replace(matching, document_id="history-doc:newer", source_ref="history-source:newer",
        occurred_at=(NOW - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    assert [doc.document_id for doc, _ in reader.search_private_history(
        caller, "warmtepomp", now=NOW, limit=3)] == [matching.document_id]
    # Both terms belong to one exchange, but neither side contains both.
    assert "option" in matching.content and "warmtepomp" not in matching.content
    assert [doc.document_id for doc, _ in reader.search_private_history(
        caller, "option warmtepomp", now=NOW, limit=3)] == [matching.document_id]
    found = reader.search_private_history(caller, "warmtepomp", now=NOW, limit=1)
    assert [doc.document_id for doc, _ in found] == [matching.document_id]
    assert "warmtepomp" in _exchange_content(SimpleNamespace(_journal=lambda turn: journal), found[0][0])
    assert reader.search_private_history(replace(caller, principal_id="other-person",
        audience=MemoryAudience.private_person(principal_id="other-person")), "warmtepomp", now=NOW) == ()
    shared = replace(caller, conversation_id="household:home:conversation:test",
        message_id="household:home:conversation:test:request", audience=MemoryAudience.household(principal_id=str(owner), household_id="home",
        consent_ref=HouseholdConsentReference(str(owner), "home", "household-consent:test")))
    assert reader.search_private_history(shared, "warmtepomp", now=NOW) == ()
    telegram = replace(caller, principal_id="telegram:101", conversation_id="telegram:101",
        audience=MemoryAudience.private_person(principal_id="telegram:101"))
    assert reader.search_private_history(telegram, "warmtepomp", now=NOW)[0][0].document_id == matching.document_id
