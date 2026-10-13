"""Overlapping durable turns retain their question/answer correlation."""
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationTurn, RecentConversationMessage, IdentityProfile
from tests.model.test_pydantic_conversation_model import _memory


def test_out_of_order_answers_keep_their_original_question_in_model_context(tmp_path):
    turn = ConversationTurn.private("tenant", "installation", "jaap", "chat", "third", "Continue", "2026-09-23T12:04:00Z")
    rows = [NS(event_type=kind, occurred_at=datetime(2026,9,23,12,minute,tzinfo=UTC),
               payload={"data":{"text":text,"message_id":message}})
            for minute,kind,message,text in [(0,"INGRESS_COMMITTED","a","Research A"),
               (1,"INGRESS_COMMITTED","b","Quick question B"),
               (2,"REPLY_COMMITTED","b","Quick answer B"),
               (3,"REPLY_COMMITTED","a","Research answer A")]]
    connection = NS(begin=lambda:NS(rollback=lambda:None),execute=lambda _:NS(all=lambda:list(reversed(rows))))
    journal=object.__new__(PostgresConversationJournal)
    journal._engine=NS(connect=lambda:nullcontext(connection))
    journal._scope_value=TimelineScope(uuid4(),uuid4())
    journal._scope=lambda _:None
    recent=journal.recent(turn)
    assert [r.text for r in recent] == [r.payload["data"]["text"] for r in rows]
    assert recent[0].source_turn_sha256 == recent[3].source_turn_sha256
    assert recent[1].source_turn_sha256 == recent[2].source_turn_sha256
    assert recent[0].source_turn_sha256 != recent[1].source_turn_sha256
    from eva.adapters.model.pydantic_conversation import _recent_message_line
    labels={recent[0].source_turn_sha256:1,recent[1].source_turn_sha256:2}
    lines=[_recent_message_line(r,labels[r.source_turn_sha256]) for r in recent]
    assert lines == ["Jaap [beurt 1]: Research A", "Jaap [beurt 2]: Quick question B",
                     "Eva [beurt 2]: Quick answer B", "Eva [beurt 1]: Research answer A"]
    model=PydanticConversationModel(auth_file=tmp_path/"unused",model="fixture")
    instructions=model._instructions(IdentityProfile("Eva","Huisgenoot",("Wees eerlijk",),160),_memory(),recent)
    for line in lines: assert line in instructions
    assert recent[0].source_turn_sha256 not in instructions



def test_legacy_and_interrupted_messages_keep_existing_playback_limits():
    from eva.adapters.model.pydantic_conversation import _recent_message_line
    legacy=RecentConversationMessage("Eva","Answer","2026-09-23T12:00:00Z")
    assert _recent_message_line(legacy)=="Eva: Answer"
    interrupted=RecentConversationMessage("Eva","Answer","2026-09-23T12:00:00Z","interrupted","a"*64)
    line=_recent_message_line(interrupted,1)
    assert "[beurt 1]" in line and "afspelen onderbroken" in line
    with pytest.raises(ValueError):
        RecentConversationMessage("Eva","Answer","2026-09-23T12:00:00Z",source_turn_sha256="invalid")
