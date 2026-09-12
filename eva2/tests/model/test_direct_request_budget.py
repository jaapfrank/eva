"""Regression for the existing per-generate cap, not a new whole-turn budget."""

from dataclasses import replace
from contextlib import asynccontextmanager

from eva.application.conversation import ConversationModelUsage, observe_model_requests

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from tests.model.test_pydantic_conversation_model import (
    _ExactStreamUsageModel,
    _adapter,
    _identity,
    _memory,
    _runtime_budget,
    _turn,
)
from tests.support.scripted_pydantic_conversation import (
    ScriptedPhase,
    ScriptedPydanticConversation,
)


def test_language_repair_does_not_dispatch_after_existing_input_cap_exhausted(tmp_path):
    budget = _runtime_budget(max_input_tokens=16_000)
    scripted = ScriptedPydanticConversation(
        (ScriptedPhase.text("Привет"), ScriptedPhase.text("Hallo Jaap."))
    )
    model = _adapter(tmp_path, scripted, resource_budget=budget)
    model._model_backend = _ExactStreamUsageModel(
        scripted.model, input_tokens=budget.max_input_tokens
    )
    identity = replace(_identity(), identity_pack=load_current_identity_pack())

    class Journal:
        admissions = 0
        usages = []

        def begin_model_request(self, turn):
            self.admissions += 1
            return str(self.admissions)

        def complete_model_request(self, turn, request_id, usage):
            self.usages.append(usage)

    journal = Journal()
    with (
        observe_model_requests(_turn(), journal),
        pytest.raises(ConversationModelError, match="input budget") as caught,
    ):
        model.generate(_turn(), identity, _memory(), ())
    assert journal.admissions == 1 and len(journal.usages) == 1
    assert caught.value.model_usage == journal.usages[0]
    # generate already rejects aggregate input above this existing cap. Once
    # the first phase exhausts it, a repair cannot fit: reject before dispatch.
    assert len(scripted.captures) == 1
    assert caught.value.model_usage.input_tokens == budget.max_input_tokens


def test_language_repair_below_cap_keeps_existing_admission(tmp_path):
    budget = _runtime_budget(max_input_tokens=16_000)
    scripted = ScriptedPydanticConversation(
        (ScriptedPhase.text("Привет"), ScriptedPhase.text("Hallo Jaap."))
    )

    class Inputs(_ExactStreamUsageModel):
        inputs = iter((15_999, 1))

        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            self._input_tokens = next(self.inputs)
            async with super().request_stream(*args, **kwargs) as response:
                yield response

    model = _adapter(tmp_path, scripted, resource_budget=budget)
    model._model_backend = Inputs(scripted.model, input_tokens=0)
    identity = replace(_identity(), identity_pack=load_current_identity_pack())
    result = model.generate(_turn(), identity, _memory(), ())
    assert result.text == "Hallo Jaap." and len(scripted.captures) == 2
    assert result.usage.input_tokens == 16_000


def test_unknown_usage_does_not_become_exhausted_or_zero(tmp_path, monkeypatch):
    from eva.application.conversation import ConversationModelTiming

    model = _adapter(
        tmp_path,
        ScriptedPydanticConversation((ScriptedPhase.text("unused"),)),
        resource_budget=_runtime_budget(),
    )
    unknown = ConversationModelUsage.unmeasured()
    calls = []

    def phase(**kwargs):
        calls.append(kwargs)
        return (
            "Привет" if len(calls) == 1 else "Hallo Jaap.",
            None,
            unknown,
            ConversationModelTiming.unmeasured(),
        )

    monkeypatch.setattr(model, "_run_pydantic_phase", phase)
    identity = replace(_identity(), identity_pack=load_current_identity_pack())
    with pytest.raises(ConversationModelError, match="usage is unmeasured"):
        model.generate(_turn(), identity, _memory(), ())
    assert len(calls) == 2
    assert unknown.input_tokens is None  # Existing validation, no invented reserve.


def test_original_phase_failure_and_known_usage_are_unchanged(tmp_path, monkeypatch):
    model = _adapter(
        tmp_path,
        ScriptedPydanticConversation((ScriptedPhase.text("unused"),)),
        resource_budget=_runtime_budget(),
    )
    usage = ConversationModelUsage.measured(
        model_attempts=1, tool_calls=0, input_tokens=3, output_tokens=2
    )
    original = ConversationModelError(
        "original input budget failure", model_usage=usage
    )
    calls = []

    def phase(**kwargs):
        calls.append(kwargs)
        raise original

    monkeypatch.setattr(model, "_run_pydantic_phase", phase)
    with pytest.raises(ConversationModelError) as caught:
        model.generate(_turn(), _identity(), _memory(), ())
    assert caught.value is original and caught.value.model_usage is usage
    assert len(calls) == 1
