"""Slow recall can overlap one bounded reaction without bypassing memory."""
import asyncio
from dataclasses import replace
from contextlib import nullcontext
from threading import Event

import pytest

from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationModelResult,
    ConversationModelUsage, ConversationStopped, ConversationTurn, IdentityProfile,
    LivingConversation, publish_conversation_text, stream_conversation_text,
)
from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
from eva.application.voice_context import (
    SPEAK_AND_CONTINUE, VoiceContext, VoicePreparationUnavailable, voice_context,
)
from tests.application.test_conversation import _Memory, NOW


USAGE = ConversationModelUsage.measured(tool_calls=1, input_tokens=10, output_tokens=2)
IDENTITY = IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80)


def run_case(monkeypatch, *, kind="speak", fast=False, pending=False, stop=False,
             recall_error=False, journal=None, signal_race=False):
    monkeypatch.setattr("eva.application.conversation._VOICE_PREPARATION_DELAY_SECONDS", 0.01)
    release, prepared = Event(), Event()
    signal = CancellationSignal() if signal_race else None
    events, calls, previews = [], [], []
    if fast or pending:
        release.set()

    class Memory(_Memory):
        def recall_for_presence(self, query):
            assert release.wait(1), "Preparation did not release its read lane"
            if recall_error:
                raise RuntimeError("source authority rejected")
            return replace(super().recall_for_presence(query), style_mirror="complete scoped memory")

    class Model:
        def generate_voice_preparation(self, turn, identity, recent, *, self_context=()):
            calls.append("preparation")
            assert identity is IDENTITY and turn.principal_id == "jaap"
            prepared.set()
            try:
                if kind == "cancel":
                    raise asyncio.CancelledError()
                if kind == "unavailable":
                    error = VoicePreparationUnavailable()
                    error.model_usage = USAGE
                    raise error
                if kind == "abstain":
                    publish_conversation_text("Never publish this proposed final", tools_disabled=True)
                    return ConversationModelResult("stilte", "test", usage=USAGE)
                text = "Even terugdenken." if kind != "long" else "een twee drie vier vijf zes zeven acht negen"
                if kind == "oversize":
                    text = "a" * 1001
                if signal is not None:
                    signal.cancel()
                name = SPEAK_AND_CONTINUE if kind != "external" else "change_a_device"
                return ConversationModelResult(None, "test", ConversationActionCall("thought", name, {"text": text}), usage=USAGE)
            finally:
                if kind != "speak" or stop or signal_race:
                    release.set()

        def generate(self, turn, identity, memory, recent, *, completed_action=None, available_actions=(), **kwargs):
            calls.append("answer")
            assert memory.style_mirror == "complete scoped memory"
            if kind == "speak" and not fast and not pending:
                assert completed_action.call.name == SPEAK_AND_CONTINUE
                assert SPEAK_AND_CONTINUE not in available_actions
            return ConversationModelResult("Nu heb ik de herinnering erbij.", "test", usage=USAGE)

    class Actions:
        action_names = ()
        def pending(self, turn):
            return ConversationActionReceipt(ConversationActionCall("old", "already_read", {}), {"status": "verified"}) if pending else None
        def active_context(self, turn): return ()
        def continuation_action_names(self, turn): return ()

    def event(value):
        events.append(value)
        assert not release.is_set(), "Reaction arrived after the slow read completed"
        release.set()

    def checkpoint(turn, usage):
        if stop and prepared.is_set():
            raise ConversationStopped()

    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Wat deed je vandaag?", NOW)
    model = Model()
    with cancellation_scope(signal) if signal is not None else nullcontext(), \
            voice_context(VoiceContext((), (), lambda _: None, event)), stream_conversation_text(previews.append):
        result = LivingConversation(Memory(), model, IDENTITY, Actions(),
                                    contact_model=model, before_step=None if signal_race else checkpoint).respond(turn, model_journal=journal)
    return result, calls, events, previews


def test_reaction_precedes_full_memory_but_answer_uses_it(monkeypatch):
    result, calls, events, previews = run_case(monkeypatch)
    assert calls == ["preparation", "answer"]
    assert [e["type"] for e in events] == ["spoken_update"]
    assert result.model_usage.model_attempts == 2
    assert result.model_usage.input_tokens == 20
    assert previews == []


@pytest.mark.parametrize("kind", ["abstain", "long", "oversize", "external", "unavailable"])
def test_unusable_reaction_has_no_effect_but_normal_recall_continues(monkeypatch, kind):
    result, calls, events, previews = run_case(monkeypatch, kind=kind)
    assert calls == ["preparation", "answer"] and result.text
    assert events == previews == []
    assert result.model_usage.model_attempts == 2


@pytest.mark.parametrize("mode", ["fast", "pending"])
def test_fast_or_replayed_turn_has_no_preparation_request(monkeypatch, mode):
    result, calls, events, _ = run_case(monkeypatch, **{mode: True})
    assert calls == ["answer"] and events == []
    assert result.model_usage.model_attempts == 1


def test_stop_between_control_generation_and_publish_prevents_speech(monkeypatch):
    with pytest.raises(ConversationStopped):
        run_case(monkeypatch, stop=True)


def test_cancelled_preparation_propagates(monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        run_case(monkeypatch, kind="cancel")


def test_recall_failure_after_a_reaction_never_produces_an_answer(monkeypatch):
    with pytest.raises(RuntimeError, match="source authority rejected"):
        run_case(monkeypatch, recall_error=True)


def test_both_phases_are_admitted_and_measured_once(monkeypatch):
    class Journal:
        admissions, results = [], []
        def begin_model_request(self, turn):
            key = str(len(self.admissions)); self.admissions.append(key); return key
        def complete_model_request(self, turn, key, usage, **kwargs):
            self.results.append((key, usage))
        def model_usage(self, turn):
            return ConversationModelUsage.aggregate(tuple(u for _, u in self.results))
    journal = Journal()
    result, _, _, _ = run_case(monkeypatch, journal=journal)
    assert journal.admissions == ["0", "1"]
    assert [key for key, _ in journal.results] == ["0", "1"]
    assert result.model_usage.model_attempts == 2


def test_owner_signal_after_preparation_prevents_speech_without_checkpoint_callback(monkeypatch):
    with pytest.raises(WorkInterrupted):
        run_case(monkeypatch, signal_race=True)
