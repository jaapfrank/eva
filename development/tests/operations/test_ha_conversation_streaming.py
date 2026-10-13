"""Exercise the real HA entity with only its Home Assistant boundary stubbed."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def integration(monkeypatch):
    modules = {}
    for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.conversation",
        "homeassistant.helpers", "homeassistant.helpers.entity_registry", "homeassistant.helpers.intent",
    ):
        modules[name] = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, modules[name])

    class ConversationEntity:
        # Match HA 2026.9.2's default and public property, not Eva's override.
        _attr_supports_streaming = False

        @property
        def supports_streaming(self):
            return self._attr_supports_streaming

    spoken = []

    class IntentResponse:
        def __init__(self, language):
            self.language = language
            self.speech = {}

        def async_set_speech(self, text):
            spoken.append(text)
            self.speech = {"plain": {"speech": text}}

    modules["homeassistant.components.conversation"].ConversationEntity = ConversationEntity
    modules["homeassistant.components.conversation"].ConversationResult = SimpleNamespace
    modules["homeassistant.helpers.intent"].IntentResponse = IntentResponse
    satellite = SimpleNamespace(device_id="a" * 32, disabled_by=None)
    modules["homeassistant.helpers.entity_registry"].async_get = lambda hass: SimpleNamespace(
        async_get=lambda entity_id: satellite if entity_id == "assist_satellite.room" else None,
    )
    source = Path(__file__).parents[3] / "deploy/home-assistant/custom_components/eva2_voice"
    package = ModuleType("ha_streaming_test")
    package.__path__ = [str(source)]
    package.DOMAIN = "eva2_voice"
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in ("client", "conversation"):
        spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", source / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    return module, spoken


class ChatLog:
    conversation_id = "bound-conversation"

    def __init__(self):
        self.deltas = []
        self.answer_seen = asyncio.Event()

    async def async_add_delta_content_stream(self, entity_id, stream):
        assert entity_id == "conversation.eva2"
        async for delta in stream:
            self.deltas.append(delta)
            if delta.get("content") == "The answer ":
                self.answer_seen.set()
            yield delta


def entity_and_input(module, client):
    entity = module.EvaConversation(SimpleNamespace(entry_id="entry"), client)
    entity.entity_id = "conversation.eva2"
    entity.hass = SimpleNamespace(config=SimpleNamespace(language="nl"))
    user_input = SimpleNamespace(
        satellite_id="assist_satellite.room", language="nl", text="A household question",
    )
    return entity, user_input


def test_answer_stream_excludes_transport_receipt_before_bound_final_response(integration):
    module, spoken = integration

    async def run():
        release = asyncio.Event()

        async def respond(**request):
            assert {key: request[key] for key in (
                "satellite_id", "device_id", "conversation_id", "text", "language",
            )} == {
                "satellite_id": "assist_satellite.room", "device_id": "a" * 32,
                "conversation_id": "bound-conversation", "text": "A household question", "language": "nl",
            }
            assert request.get("on_receipt") is None
            await request["on_delta"]("The answer ")
            await release.wait()
            await request["on_delta"]("is ready.")
            return SimpleNamespace(text="The answer is ready.", continue_conversation=True)

        entity, user_input = entity_and_input(module, SimpleNamespace(respond=respond))
        assert entity.supports_streaming is True
        log = ChatLog()
        task = asyncio.create_task(entity._async_handle_message(user_input, log))
        try:
            await asyncio.wait_for(log.answer_seen.wait(), 2)
            assert not task.done() and spoken == []
            assert log.deltas == [
                {"role": "assistant"}, {"content": "The answer "},
            ]
            release.set()
            result = await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert result.conversation_id == "bound-conversation"
        assert result.continue_conversation is True
        assert result.response.language == "nl"
        assert result.response.speech == {"plain": {"speech": "The answer is ready."}}
        assert spoken == ["The answer is ready."]
        assert "".join(delta.get("content", "") for delta in log.deltas) == spoken[0]

    asyncio.run(run())


def test_cancellation_drops_late_client_output_without_final_utterance(integration):
    module, spoken = integration

    async def run():
        cancelled = asyncio.Event()

        async def respond(**request):
            assert request.get("on_receipt") is None
            await request["on_delta"]("The answer ")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                # A late completion must not become speech even if cancellation is ignored.
                await request["on_delta"]("must not be spoken.")
                return SimpleNamespace(text="Late answer", continue_conversation=True)

        entity, user_input = entity_and_input(module, SimpleNamespace(respond=respond))
        log = ChatLog()
        task = asyncio.create_task(entity._async_handle_message(user_input, log))
        try:
            await asyncio.wait_for(log.answer_seen.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert cancelled.is_set()
        assert spoken == []
        assert log.deltas == [
            {"role": "assistant"}, {"content": "The answer "},
        ]

    asyncio.run(run())
