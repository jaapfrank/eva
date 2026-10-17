"""Thinking delivery is immediately discoverable without requiring it for answers."""
import json
import httpx2
from eva.adapters.model.openai_codex_provider import _codex_compatible_request
from eva.adapters.model.pydantic_conversation import _descriptor_tool
from eva.application.voice_context import VoiceContext, voice_context, SPEAK_DESCRIPTOR
from tests.model.test_tool_namespaces import tool


def test_native_catalog_exposes_authorized_speech_without_search():
    speech = _descriptor_tool(SPEAK_DESCRIPTOR)
    body = {"model": "gpt-6-sol", "store": False, "stream": True, "instructions": "Answer",
            "input": [{"role": "user", "content": "Wat deed je vandaag?"}], "tools": [speech, tool("read_recent_activity")]}
    request = httpx2.Request("POST", "https://example.invalid/responses", json=body)
    result = json.loads(_codex_compatible_request(request, namespace_functions=True).content)
    direct = next(t for t in result["tools"] if t.get("name") == SPEAK_DESCRIPTOR.name)
    assert direct == {**speech, "defer_loading": False}
    activity = next(t for t in result["tools"] if t.get("type") == "namespace")
    assert activity["tools"][0]["name"] == "read_recent_activity"
    assert activity["tools"][0]["defer_loading"] is True
    body["tools"] = [tool("read_recent_activity")]
    result = json.loads(_codex_compatible_request(
        httpx2.Request("POST", "https://example.invalid/responses", json=body), namespace_functions=True).content)
    assert SPEAK_DESCRIPTOR.name not in json.dumps(result["tools"])


def test_injected_model_can_answer_directly_with_eager_speech_available(tmp_path):
    from tests.model.test_pydantic_conversation_model import _adapter, _turn, _identity, _memory
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase
    class Capture(ScriptedPydanticConversation):
        def _capture(self, messages, info):
            self.definitions = info.function_tools
            return super()._capture(messages, info)
    scripted = Capture((ScriptedPhase.text("Goedemorgen."),))
    model = _adapter(tmp_path, scripted)
    with voice_context(VoiceContext((), (), lambda _: None, lambda _: None)):
        result = model.generate(_turn(), _identity(), _memory(), (), available_actions=(SPEAK_DESCRIPTOR.name,))
    speech = next(t for t in scripted.definitions if t.name == SPEAK_DESCRIPTOR.name)
    assert speech.defer_loading is False
    assert result.text == "Goedemorgen." and result.action_call is None
    assert "vóór die stap" in scripted.captures[0].instructions


def test_only_eager_speech_has_no_native_discovery_tool():
    speech = _descriptor_tool(SPEAK_DESCRIPTOR)
    for extra in ([], [{"type": "tool_search"}]):
        body = {"store": False, "stream": True, "instructions": "Prepare", "input": [{"role": "user", "content": "Hallo"}],
                "tools": [*extra, speech]}
        result = json.loads(_codex_compatible_request(httpx2.Request(
            "POST", "https://example.invalid/responses", json=body), namespace_functions=True).content)
        assert result["tools"] == [{**speech, "defer_loading": False}]


def test_eager_plus_existing_deferred_namespace_keeps_discovery():
    speech = _descriptor_tool(SPEAK_DESCRIPTOR)
    namespace = {"type": "namespace", "name": "existing", "description": "Existing",
                 "tools": [{**tool("existing_read"), "defer_loading": True}]}
    body = {"store": False, "stream": True, "instructions": "Prepare", "input": [{"role": "user", "content": "Hallo"}],
            "tools": [speech, namespace]}
    result = json.loads(_codex_compatible_request(httpx2.Request(
        "POST", "https://example.invalid/responses", json=body), namespace_functions=True).content)
    assert result["tools"] == [{"type": "tool_search"}, {**speech, "defer_loading": False}, namespace]
