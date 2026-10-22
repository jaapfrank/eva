"""Normal STOP/cancellation and a duplicate live turn never promise repair."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from eva.application.conversation import ConversationStopped, ConversationConflict
from eva.application.cancellation import WorkInterrupted
from eva.bootstrap.self_repair import repair_failed_turn


@pytest.mark.parametrize('error',[ConversationStopped('stop'),WorkInterrupted('stop')])
def test_cancellation_never_enters_repair(error):
    def unexpected(*args):raise AssertionError('Repair must not be entered')
    runtime=SimpleNamespace(_self_repair=SimpleNamespace(observe=unexpected))
    assert repair_failed_turn(runtime,None,error) is None


def test_busy_original_execution_does_not_adopt_a_repair():
    calls=[]
    @contextmanager
    def busy(turn):
        raise ConversationConflict('conversation message is already running')
        yield
    runtime=SimpleNamespace(_self_repair=SimpleNamespace(observe=lambda *args:calls.append(args)),
        _journal=lambda turn:SimpleNamespace(execution=busy))
    assert repair_failed_turn(runtime,None,RuntimeError('private error')) is None
    assert calls==[]
