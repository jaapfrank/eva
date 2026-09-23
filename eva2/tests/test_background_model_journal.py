from dataclasses import replace
from unittest.mock import Mock

import pytest

from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
from eva.application.conversation import ConversationConflict, ConversationModelUsage, ConversationTurn


def test_background_accounting_separates_jobs_and_never_admits_user_ingress():
    source = ConversationTurn.private("tenant", "installation", "owner", "chat", "message",
                                      "Do two jobs", "2026-09-23T12:00:00Z")
    journal = Mock()
    journal._begin_model_request.return_value = "request"
    first = BackgroundModelJournal(journal, source, work_id="work:first")
    second = BackgroundModelJournal(journal, source, work_id="work:second")
    assert first.begin_model_request(source) == "request"
    first_turn = journal._begin_model_request.call_args.args[0]
    second.begin_model_request(source)
    second_turn = journal._begin_model_request.call_args.args[0]
    assert first_turn != second_turn
    assert first_turn.message_id != source.message_id
    assert first_turn.conversation_id != source.conversation_id
    assert first_turn.principal_id == source.principal_id
    BackgroundModelJournal(journal, source, work_id="work:first").begin_model_request(source)
    assert journal._begin_model_request.call_args.args[0] == first_turn
    journal.admit.assert_not_called()
    journal.committed_reply.assert_not_called()
    usage = ConversationModelUsage.unmeasured()
    first.complete_model_request(source, "request", usage, outcome="failed", local_stream_closed=True)
    assert journal.complete_model_request.call_args.args == (first_turn, "request", usage)
    assert journal.complete_model_request.call_args.kwargs == {"outcome": "failed", "local_stream_closed": True}
    first.model_usage(source)
    journal.model_usage.assert_called_once_with(first_turn)
    for changed in (replace(source, text="different task"), replace(source, principal_id="other",
                         audience=source.audience.__class__.private_person(principal_id="other"))):
        with pytest.raises(ConversationConflict):
            first.begin_model_request(changed)
