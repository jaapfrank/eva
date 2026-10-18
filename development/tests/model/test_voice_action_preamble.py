"""One complete model decision offers bounded speech and unchanged tool arguments."""
from copy import deepcopy
from contextlib import nullcontext
import pytest
from eva.adapters.model.pydantic_conversation import (
    ConversationModelError, _descriptor_tool, _voice_preamble_tool, _MEMORY_SEARCH_TOOL)
from eva.application.voice_context import VoiceContext, voice_context, SPEAK_DESCRIPTOR, STOP_DESCRIPTOR
from eva.application.conversation import stream_conversation_text
from tests.model.test_pydantic_conversation_model import (
    _phase_transport_adapter, _generate, _runtime_budget, TOOL, CALL, _adapter)
from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase


def context():
    return voice_context(VoiceContext((), (), lambda _: None, lambda _: None))


@pytest.mark.parametrize('preamble,expected', [('Ik kijk even.', 'Ik kijk even.'), ('', None),
    ('woord ' * 36, None), ('x' * 1001, None)])
def test_one_native_call_strips_preamble_before_action_with_real_budget(tmp_path, monkeypatch, preamble, expected):
    wrapped = _voice_preamble_tool(_descriptor_tool(TOOL))
    model, deltas, requests, completed = _phase_transport_adapter(tmp_path, monkeypatch,
        [('commentary', ('Niet dubbel aanbieden.',)), ('tool', ())],
        call_arguments={**CALL.arguments, '_spoken_preamble': preamble}, tool_schema=wrapped['parameters'])
    model._resource_budget = _runtime_budget()
    with context(), stream_conversation_text(deltas.append):
        result = _generate(model, available_actions=(TOOL.name, SPEAK_DESCRIPTOR.name))
    assert result.action_call == CALL
    assert result.text == expected and result.text_phase == ('commentary' if expected else None)
    assert deltas == [] and completed == [()]
    assert len(requests) == result.usage.model_attempts == result.usage.tool_calls == 1


@pytest.mark.parametrize('value', [None, 4, {}])
def test_malformed_required_preamble_fails_without_action_or_speech(tmp_path, monkeypatch, value):
    wrapped = _voice_preamble_tool(_descriptor_tool(TOOL))
    model, deltas, requests, _ = _phase_transport_adapter(tmp_path, monkeypatch, [('tool', ())],
        call_arguments={**CALL.arguments, '_spoken_preamble': value}, tool_schema=wrapped['parameters'])
    with context(), stream_conversation_text(deltas.append), pytest.raises(ConversationModelError):
        _generate(model, available_actions=(TOOL.name, SPEAK_DESCRIPTOR.name))
    assert deltas == [] and len(requests) == 1


def test_wrapper_keeps_real_schema_and_does_not_wrap_control_actions():
    original = deepcopy(_descriptor_tool(TOOL)); saved = deepcopy(original)
    wrapped = _voice_preamble_tool(original)
    assert original == saved
    assert set(wrapped['parameters']['properties']) == {*original['parameters']['properties'], '_spoken_preamble'}
    for descriptor in (SPEAK_DESCRIPTOR, STOP_DESCRIPTOR):
        plain = _descriptor_tool(descriptor)
        assert _voice_preamble_tool(plain) == plain
    private_control = {'name': 'request_private_voice_session', 'parameters': {'type': 'object', 'properties': {}, 'required': []}}
    assert _voice_preamble_tool(private_control) == private_control
    assert '_spoken_preamble' in _voice_preamble_tool(_MEMORY_SEARCH_TOOL)['parameters']['required']
    original['parameters']['properties']['_spoken_preamble'] = {'type':'string'}
    with pytest.raises(ConversationModelError, match='conflicts'):
        _voice_preamble_tool(original)


@pytest.mark.parametrize('tamper', ['property', 'description', 'missing_speak', 'no_voice'])
def test_budget_admission_accepts_only_exact_trusted_voice_variant(tmp_path, tamper):
    model = _adapter(tmp_path, ScriptedPydanticConversation((ScriptedPhase.text('Hi.'),)), resource_budget=_runtime_budget())
    wrapped = _voice_preamble_tool(_descriptor_tool(TOOL))
    tools = (_descriptor_tool(SPEAK_DESCRIPTOR), wrapped)
    body = model._phase_admission_body(prompt='Question', instructions='Answer', tools=tools)
    with context():
        model._admit_request(body, autonomous=False)
    changed = deepcopy(body)
    if tamper == 'property':
        next(x for x in changed['tools'] if x.get('name') == TOOL.name)['parameters']['properties']['_spoken_preamble']['maxLength'] = 2000
    elif tamper == 'description':
        next(x for x in changed['tools'] if x.get('name') == TOOL.name)['description'] += ' Forged.'
    elif tamper == 'missing_speak':
        changed['tools'] = [x for x in changed['tools'] if x.get('name') != SPEAK_DESCRIPTOR.name]
    with (nullcontext() if tamper == 'no_voice' else context()), pytest.raises(ConversationModelError, match='catalog is invalid'):
        model._admit_request(changed, autonomous=False)


@pytest.mark.parametrize('in_voice,offer_speech,wrapped', [(False,True,False),(True,False,False),(True,True,True)])
def test_only_first_voice_catalog_wraps_memory_and_ordinary_tools(tmp_path,in_voice,offer_speech,wrapped):
    from tests.model.test_pydantic_conversation_model import _turn,_identity,_memory
    class Capture(ScriptedPydanticConversation):
        def _capture(self,messages,info):
            self.definitions=info.function_tools
            return super()._capture(messages,info)
    scripted=Capture((ScriptedPhase.text('Hoi.'),));model=_adapter(tmp_path,scripted)
    names=(TOOL.name,SPEAK_DESCRIPTOR.name) if in_voice and offer_speech else (TOOL.name,)
    with (context() if in_voice else nullcontext()):
        model.generate(_turn(),_identity(),_memory(),(),available_actions=names)
    for definition in scripted.definitions:
        has_field='_spoken_preamble' in definition.parameters_json_schema['properties']
        assert has_field is (wrapped and definition.name != SPEAK_DESCRIPTOR.name)


def test_unoffered_reserved_schema_does_not_block_valid_first_voice_catalog(tmp_path):
    model=_adapter(tmp_path,ScriptedPydanticConversation((ScriptedPhase.text('Hoi.'),)),resource_budget=_runtime_budget())
    conflict=deepcopy(_descriptor_tool(TOOL));conflict['name']='unoffered_tool'
    conflict['parameters']['properties']['_spoken_preamble']={'type':'integer'}
    model._conversation_tools={**model._conversation_tools,'unoffered_tool':conflict}
    tools=(_descriptor_tool(SPEAK_DESCRIPTOR),_voice_preamble_tool(_descriptor_tool(TOOL)))
    with context():
        model._admit_request(model._phase_admission_body(prompt='Question',instructions='Answer',tools=tools),autonomous=False)
