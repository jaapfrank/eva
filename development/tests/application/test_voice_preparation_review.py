"""Independent regression: STOP after provider accounting forbids late speech."""
import pytest
from eva.application.cancellation import CancellationSignal, cancellation_scope, WorkInterrupted
from eva.application.conversation import ConversationModelUsage
from eva.application.voice_context import VoiceContext
from tests.application.test_voice_preparation_flow import run_case


def test_stop_signal_after_preparation_accounting_prevents_publish(monkeypatch):
    signal = CancellationSignal()
    published = []
    original = VoiceContext.speak_and_continue
    def observe(self, call):
        published.append(call)
        return original(self, call)
    monkeypatch.setattr(VoiceContext, "speak_and_continue", observe)
    class Journal:
        usages = []
        def begin_model_request(self, turn):
            return str(len(self.usages))
        def complete_model_request(self, turn, key, usage, **kwargs):
            self.usages.append(usage)
            signal.cancel()
        def model_usage(self, turn):
            return ConversationModelUsage.aggregate(tuple(self.usages))
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        run_case(monkeypatch, journal=Journal())
    assert published == []
