import pytest
from eva.application.conversation import RecentConversationMessage
from eva.adapters.model.pydantic_conversation import _recent_message_line

@pytest.mark.parametrize('status',[None,'completed','unconfirmed','interrupted'])
def test_recent_message_keeps_original_time_and_playback_evidence(status):
    message=RecentConversationMessage('Eva','Ik heb de inventaris bekeken.',
        '2026-10-15T23:30:00Z',playback_status=status,source_turn_sha256='a'*64)
    line=_recent_message_line(message,2)
    assert '[berichttijd UTC: 2026-10-15T23:30:00Z]' in line
    assert '[beurt 2]' in line and message.text in line
    if status is not None:
        assert 'gegenereerd antwoord' in line
        assert 'afspelen' in line
