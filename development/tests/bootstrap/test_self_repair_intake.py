"""Normal STOP/cancellation and a duplicate live turn never promise repair."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from eva.adapters.model.contracts import ConversationModelRateLimited, ConversationModelUnavailable
from eva.application.conversation import ConversationStopped, ConversationConflict
from eva.application.cancellation import WorkInterrupted
from eva.bootstrap.self_repair import repair_failed_turn


@pytest.mark.parametrize('error',[ConversationStopped('stop'),WorkInterrupted('stop')])
def test_cancellation_never_enters_repair(error):
    def unexpected(*args):raise AssertionError('Repair must not be entered')
    runtime=SimpleNamespace(_self_repair=SimpleNamespace(observe=unexpected))
    assert repair_failed_turn(runtime,None,error) is None


@pytest.mark.parametrize("error_type", [ConversationModelRateLimited, ConversationModelUnavailable])
def test_provider_refusal_stays_on_the_original_failure_route(error_type, monkeypatch):
    from contextlib import nullcontext
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    error = error_type("provider failure")

    def fail(turn, recent_messages):
        raise error

    def unexpected_repair(*args):
        pytest.fail("A provider refusal must not enter self-repair")

    monkeypatch.setattr("eva.bootstrap.self_repair.repair_failed_turn", unexpected_repair)
    runtime = SimpleNamespace(
        _cold_path=SimpleNamespace(foreground=nullcontext),
        _respond_admitted=fail,
    )

    with pytest.raises(error_type) as raised:
        PostgresHindsightMemoryRuntime.respond(runtime, object())
    assert raised.value is error


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
