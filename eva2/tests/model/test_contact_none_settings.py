"""Only the explicitly configured Luna contact path admits reasoning none."""
from pathlib import Path

import pytest

from eva.adapters.model.openai_codex_provider import OpenAICodexProviderSettings
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.bootstrap.runtime import RuntimeSettings
from tests.bootstrap.test_runtime_settings import _minimum


def test_contact_none_environment_keeps_work_model(monkeypatch, private_tmp_path):
    _minimum(monkeypatch, private_tmp_path)
    monkeypatch.setenv("EVA_CONVERSATION_MODEL", "gpt-6-astra")
    monkeypatch.setenv("EVA_CONVERSATION_REASONING_EFFORT", "medium")
    monkeypatch.setenv("EVA_CONVERSATION_CONTACT_MODEL", "gpt-5.6-luna")
    monkeypatch.delenv("EVA_CONVERSATION_CONTACT_REASONING_EFFORT", raising=False)
    assert RuntimeSettings.from_environment().conversation_contact_reasoning_effort == "low"
    monkeypatch.setenv("EVA_CONVERSATION_CONTACT_REASONING_EFFORT", "none")
    settings = RuntimeSettings.from_environment()
    assert settings.conversation_contact_reasoning_effort == "none"
    assert settings.conversation_model == "gpt-6-astra"
    assert settings.conversation_reasoning_effort == "medium"
    monkeypatch.setenv("EVA_CONVERSATION_REASONING_EFFORT", "none")
    with pytest.raises(RuntimeError, match="REASONING_EFFORT"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("model", [None, "gpt-6-astra", "gpt-5.5", "unknown"])
def test_runtime_rejects_none_without_verified_contact_model(model):
    with pytest.raises(ValueError, match="none requires"):
        RuntimeSettings("unused", "http://memory", conversation_contact_model=model,
                        conversation_contact_reasoning_effort="none")


def test_runtime_rejects_none_for_work_even_with_luna():
    with pytest.raises(ValueError, match="conversation reasoning"):
        RuntimeSettings("unused", "http://memory", conversation_model="gpt-5.6-luna",
                        conversation_reasoning_effort="none")


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
def test_luna_efforts_are_preserved_at_both_model_boundaries(effort):
    model = PydanticConversationModel(auth_file=Path("/unused"), model="gpt-5.6-luna", reasoning_effort=effort)
    provider = OpenAICodexProviderSettings(endpoint="https://chatgpt.com/backend-api/codex/responses",
                                         model_name="gpt-5.6-luna", reasoning_effort=effort)
    assert model._reasoning_effort == effort
    assert provider.reasoning_effort == effort


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-5.5", "unknown"])
def test_other_models_reject_none_at_both_boundaries(model):
    with pytest.raises(ValueError, match="none requires"):
        PydanticConversationModel(auth_file=Path("/unused"), model=model, reasoning_effort="none")
    with pytest.raises(ValueError, match="none requires"):
        OpenAICodexProviderSettings(endpoint="https://chatgpt.com/backend-api/codex/responses",
                                   model_name=model, reasoning_effort="none")
