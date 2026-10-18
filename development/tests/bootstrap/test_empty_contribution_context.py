"""Empty context must not perform authority reads or grant a capability."""
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.completed_contribution import CompletedContributionActions
from eva.application.conversation import ConversationConflict


def test_empty_context_does_not_authorize_discovery_or_execution():
    actions = object.__new__(CompletedContributionActions)
    calls = []
    def denied(turn):
        calls.append(turn)
        raise ConversationConflict("binding revoked")
    actions._authority = denied
    turn = SimpleNamespace(audience=SimpleNamespace(kind="private_person"),
                           conversation_id="voice:context")
    assert actions.active_context(turn) == ()
    assert calls == []
    assert actions.available_for(turn) is False
    with pytest.raises(ConversationConflict, match="binding revoked"):
        actions.execute(turn, object())
    assert calls == [turn, turn]
