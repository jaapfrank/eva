"""A pending probe reuses only its own freshly validated authority result."""
from types import SimpleNamespace as NS
import pytest
from eva.adapters.postgres import completed_contribution as contribution
from eva.providers.human_understanding_actions import HumanUnderstandingActions
from eva.application.conversation import ConversationConflict


@pytest.mark.parametrize('kind', ['contribution', 'learning'])
def test_empty_pending_checks_once_but_next_call_and_execution_recheck(kind, monkeypatch):
    calls = []
    revoked = [False]
    timeline = NS(events=lambda slot: ())
    turn = NS(audience=NS(kind='private_person'), conversation_id='voice:pending')
    authority = NS(owner=NS(_engine=object(), _scope=object()))
    def check(turn):
        calls.append(turn)
        if revoked[0]:
            raise ConversationConflict('revoked')
        return (authority, 'owner') if kind == 'contribution' else (None, None, None, timeline)
    if kind == 'contribution':
        actions = object.__new__(contribution.CompletedContributionActions)
        actions._authority = check
        actions._id = lambda turn, step: 'exact-source'
        monkeypatch.setattr(contribution, 'PostgresOperationalTimeline', lambda *args: timeline)
    else:
        actions = object.__new__(HumanUnderstandingActions)
        actions._host = check
        actions._slot = lambda turn, step: 'exact-source'
    assert actions.pending(turn) is None
    assert calls == [turn]
    assert actions.available_for(turn)
    assert calls == [turn, turn]
    revoked[0] = True
    assert actions.pending(turn) is None
    assert actions.available_for(turn) is False
    with pytest.raises(ConversationConflict, match='revoked'):
        actions.execute(turn, object())
    assert len(calls) == 5


@pytest.mark.parametrize('error', [OSError('unavailable'), ValueError('invalid'),
    ConversationConflict('revoked'), RuntimeError('initiative owner binding is not current')])
def test_contribution_pending_preserves_denied_authority_behavior(error):
    actions = object.__new__(contribution.CompletedContributionActions)
    def denied(turn):
        raise error
    actions._authority = denied
    turn = NS(audience=NS(kind='private_person'), conversation_id='voice:pending')
    assert actions.pending(turn) is None
    assert actions.available_for(turn) is False


def test_contribution_pending_does_not_swallow_unexpected_authority_error():
    actions = object.__new__(contribution.CompletedContributionActions)
    def failed(turn):
        raise RuntimeError('unexpected integrity failure')
    actions._authority = failed
    turn = NS(audience=NS(kind='private_person'), conversation_id='voice:pending')
    with pytest.raises(RuntimeError, match='unexpected integrity'):
        actions.pending(turn)
