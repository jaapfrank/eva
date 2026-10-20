"""Silent execution remains a request, never a fabricated spoken answer."""
from uuid import uuid4
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_household_public_work import setup
from tests.postgres.test_household_conversation_context import other
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import GroundedReply
from eva.bootstrap.household_continuity import HouseholdConversationContext


def test_other_session_keeps_silent_request_without_assistant_status(database):
    x=setup(database,"silent-context")
    old=x.service.turn(str(uuid4()),"Doe de buitenlamp uit")
    journal=PostgresConversationJournal(database,x.server.conversation_scope)
    journal.commit_reply(old,GroundedReply(conversation_id=old.conversation_id,
        message_id=old.message_id,text="Opdracht uitgevoerd.",evidence_memory_ids=(),
        generated_at=old.occurred_at,provider="eva:verified-action",response_mode="silent"))
    x.service.current("close")
    current=other(x);turn=current.turn(str(uuid4()),"Wat deden we daarnet?")
    found=HouseholdConversationContext(x.server).recent(turn)
    assert sum(item.text.endswith(old.text) for item in found)==1
    assert not any("Opdracht uitgevoerd" in item.text or item.role=="Eva" for item in found)
