"""Durable silent status survives replay and is excluded from dialogue history."""
from dataclasses import replace
from datetime import UTC,datetime
from types import SimpleNamespace
from uuid import UUID
import pytest
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.conversation import GroundedReply,ConversationConflict,ConversationTurn
from tests.postgres.test_conversation_journal_roles import _Connection,TENANT,INSTALLATION,NOW


def turn():return ConversationTurn.private(TENANT,INSTALLATION,"owner","conversation","message","Doe de lamp uit",NOW)


def test_silent_reply_replays_identically_without_an_execution_or_new_model():
    t=turn();reply=GroundedReply(t.conversation_id,t.message_id,"Opdracht uitgevoerd.",(),NOW,"eva:verified-action",response_mode="silent")
    data=PostgresConversationJournal._reply_data(t,reply)
    journal=object.__new__(PostgresConversationJournal)
    journal._timeline=SimpleNamespace(events=lambda _: [SimpleNamespace(event_type="REPLY_COMMITTED",payload={"data":data},occurred_at=datetime.strptime(NOW,"%Y-%m-%dT%H:%M:%SZ"))])
    assert journal.committed_reply(t)==reply
    old=replace(reply,response_mode="reply");data.clear();data.update(PostgresConversationJournal._reply_data(t,old))
    assert "response_mode" not in data and journal.committed_reply(t)==old


def test_silent_status_is_not_projected_as_an_assistant_utterance(monkeypatch):
    rows=[SimpleNamespace(event_type=kind,occurred_at=datetime(2026,10,17,tzinfo=UTC),payload={"data":data})
        for kind,data in [("REPLY_COMMITTED",{"text":"Opdracht uitgevoerd.","response_mode":"silent"}),
                          ("INGRESS_COMMITTED",{"text":"Doe de lamp uit"})]]
    connection=_Connection(rows)
    journal=PostgresConversationJournal(SimpleNamespace(connect=lambda:connection,url=SimpleNamespace(get_backend_name=lambda:"postgresql")),TimelineScope(UUID(TENANT),UUID(INSTALLATION)))
    monkeypatch.setattr(journal,"_scope",lambda c:None)
    assert [m.text for m in journal.recent(turn())]==["Doe de lamp uit"]
