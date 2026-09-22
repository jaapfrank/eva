"""Readiness pays vocabulary initialization; each request retains admission."""
from pathlib import Path

import pytest

from eva.adapters.model import pydantic_conversation as module
from eva.adapters.model.contracts import ConversationModelError
from tests.model.test_conversation_model import _runtime_budget


def test_prepare_is_local_and_does_not_bypass_per_request_budget(monkeypatch):
    calls = []
    class Tokenizer:
        def encode_ordinary(self, text):
            calls.append("encode")
            return [0] * 12001
    def load():
        calls.append("load")
        return Tokenizer()
    monkeypatch.setattr(module, "_input_tokenizer", load)
    model = module.PydanticConversationModel(auth_file=Path("/absent/no-credentials"),
        resource_budget=_runtime_budget(max_input_tokens=12000))
    model.prepare_input_validation()
    assert calls == ["load"]
    with pytest.raises(ConversationModelError, match="runtime input budget"):
        model._admit_request({"input": "still bounded", "tools": []}, autonomous=False)
    assert calls == ["load", "load", "encode"]


def test_prepare_fails_closed_when_pinned_vocabulary_is_unavailable(monkeypatch):
    def unavailable():
        raise ConversationModelError("tokenizer data is unavailable")
    monkeypatch.setattr(module, "_input_tokenizer", unavailable)
    model = module.PydanticConversationModel(auth_file=Path("/absent/no-credentials"),
        resource_budget=_runtime_budget(max_input_tokens=12000))
    with pytest.raises(ConversationModelError, match="tokenizer data"):
        model.prepare_input_validation()
