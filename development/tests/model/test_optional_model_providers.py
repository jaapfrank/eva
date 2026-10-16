"""Real upstream SDK/model protocols over a local transport; no paid requests."""
from dataclasses import replace
import json
import pytest
import httpx2
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders
from tests.model.test_pydantic_conversation_model import _turn, _identity, _memory, TOOL, CALL, RECEIPT


def configured(tmp_path, kind, handler):
    baseline=PydanticConversationModel(auth_file=tmp_path/'no-subscription-key',conversation_tools=(TOOL,))
    control=OptionalModelProviders(baseline,tmp_path/'connections',transport=httpx2.MockTransport(handler))
    identifier=control.settings.configure(kind=kind,label='Optional model',model='claude-sonnet-4-5' if kind=='anthropic' else 'test-model',
        endpoint='https://api.anthropic.com' if kind=='anthropic' else 'https://models.example.test/v1',
        api_key='synthetic-test-key-not-real',workspace_id='workspace-test' if kind=='anthropic' else '')
    return baseline,control,identifier


def anthropic_response(body, *, action=False):
    tool=body.get('tools',[{}])[0]
    value={'echoed':'eva-provider-check'}
    if action:tool={'name':TOOL.name};value=CALL.arguments
    return {'id':'msg_test','type':'message','role':'assistant','model':body['model'],
        'content':[{'type':'tool_use','id':CALL.call_id,'name':tool['name'],'input':value}],
        'stop_reason':'tool_use','stop_sequence':None,'usage':{'input_tokens':31,'output_tokens':12}}


def anthropic_stream(body, *, action):
    message=anthropic_response(body,action=action);part=message['content'][0]
    if not action:part={'type':'text','text':'Het resultaat is gecontroleerd.'}
    start={**message,'content':[],'stop_reason':None,'usage':{'input_tokens':31,'output_tokens':0}}
    initial={**part,'input':{}} if action else {**part,'text':''}
    delta={'type':'input_json_delta','partial_json':json.dumps(part['input'])} if action else {'type':'text_delta','text':part['text']}
    events=[('message_start',{'type':'message_start','message':start}),
        ('content_block_start',{'type':'content_block_start','index':0,'content_block':initial}),
        ('content_block_delta',{'type':'content_block_delta','index':0,'delta':delta}),
        ('content_block_stop',{'type':'content_block_stop','index':0}),
        ('message_delta',{'type':'message_delta','delta':{'stop_reason':'tool_use' if action else 'end_turn','stop_sequence':None},'usage':{'output_tokens':12}}),
        ('message_stop',{'type':'message_stop'})]
    return ''.join('event: '+name+'\ndata: '+json.dumps(data)+'\n\n' for name,data in events)


@pytest.mark.parametrize('kind',['anthropic','openai_compatible'])
def test_check_selection_restart_and_disable_use_real_sdk(tmp_path, kind):
    seen=[]
    def handler(request):
        body=json.loads(request.content);seen.append(body)
        assert request.headers.get('Authorization') or request.headers.get('x-api-key')
        if kind=='anthropic':
            assert request.headers['anthropic-workspace-id']=='workspace-test'
            assert 'openai_prompt_cache_key' not in body
            payload=anthropic_response(body)
        else:
            name=body['tools'][0]['function']['name']
            payload={'id':'test','object':'chat.completion','created':1,'model':body['model'],
                'choices':[{'index':0,'message':{'role':'assistant','content':None,'tool_calls':[
                    {'id':'call_test','type':'function','function':{'name':name,'arguments':'{"echoed":"eva-provider-check"}'}}]},'finish_reason':'tool_calls'}],
                'usage':{'prompt_tokens':31,'completion_tokens':12,'total_tokens':43}}
        return httpx2.Response(200,json=payload)
    baseline,control,identifier=configured(tmp_path,kind,handler)
    with pytest.raises(ValueError):control.settings.select(identifier)
    status=control.check(identifier)
    assert status['connections'][0]['check_state']=='ready'
    assert status['connections'][0]['check_usage']['model_attempts']==1
    control.settings.select(identifier)
    restored=OptionalModelProviders(baseline,control.settings.directory)
    assert restored.status()['selected_id']==identifier
    assert 'synthetic-test-key' not in json.dumps(restored.status())
    control.settings.disable(identifier)
    assert control.status()['selected_id'] is None
    with pytest.raises(RuntimeError):control.settings.connection(identifier)
    assert len(seen)==1


@pytest.mark.parametrize('kind',['anthropic','openai_compatible'])
def test_conversation_defers_action_and_consumes_same_receipt(tmp_path,kind):
    seen=[]
    def handler(request):
        body=json.loads(request.content);seen.append(body)
        assert body['stream'] is True
        if kind=='anthropic': assert 'openai_prompt_cache_key' not in body
        action=not any(item.get('role')=='user' and any(p.get('type')=='tool_result' for p in item.get('content',[]) if isinstance(p,dict)) for item in body['messages'] if isinstance(item.get('content'),list))
        if kind=='anthropic': wire=anthropic_stream(body,action=action)
        else:
            action=not any(m.get('role')=='tool' for m in body['messages'])
            delta=({'tool_calls':[{'index':0,'id':CALL.call_id,'type':'function','function':{'name':TOOL.name,'arguments':json.dumps(CALL.arguments)}}]} if action else {'content':'Het resultaat is gecontroleerd.'})
            chunk={'id':'completion-test','object':'chat.completion.chunk','created':1,'model':body['model'],
                'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls' if action else 'stop'}]}
            usage={'id':'completion-test','object':'chat.completion.chunk','created':1,'model':body['model'],'choices':[],
                'usage':{'prompt_tokens':31,'completion_tokens':12,'total_tokens':43}}
            wire='data: '+json.dumps(chunk)+'\n\ndata: '+json.dumps(usage)+'\n\ndata: [DONE]\n\n'
        return httpx2.Response(200,headers={'content-type':'text/event-stream'},text=wire)
    baseline,control,identifier=configured(tmp_path,kind,handler)
    control.settings.record_check(identifier,ready=True,usage=None);control.settings.select(identifier)
    model=OptionalConversationModel(baseline,control);turn=replace(_turn(),conversation_id='telegram:synthetic')
    first=model.generate(turn,_identity(),_memory(),(),available_actions=(TOOL.name,))
    assert first.action_call==CALL and first.provider.startswith(kind+':')
    final=model.generate(turn,_identity(),_memory(),(),completed_action=RECEIPT,available_actions=(TOOL.name,))
    assert final.text=='Het resultaat is gecontroleerd.' and final.action_call is None
    assert final.usage.input_tokens==31 and final.usage.output_tokens==12
    assert len(seen)==2


def test_rejected_connection_is_not_retried_and_never_echoes_secret(tmp_path):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx2.Response(401,json={'error':{'type':'authentication_error','message':'synthetic-test-key-not-real'}})
    _,control,identifier=configured(tmp_path,'anthropic',handler)
    assert control.check(identifier)['connections'][0]['check_state']=='failed'
    assert len(seen)==1 and 'synthetic-test-key' not in json.dumps(control.status())
    with pytest.raises(ValueError):control.settings.select(identifier)


def test_secondary_phase_uses_fresh_client_and_rechecks_disable(tmp_path):
    from eva.adapters.model.optional_backends import OptionalBackend
    from pydantic_ai import Agent
    import asyncio
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx2.Response(200,json={'id':'test','object':'chat.completion','created':1,'model':'test-model',
            'choices':[{'index':0,'message':{'role':'assistant','content':'Hoi!'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':11,'completion_tokens':2,'total_tokens':13}})
    _,control,identifier=configured(tmp_path,'openai_compatible',handler)
    backend=OptionalBackend(control.settings,identifier,transport=control.transport)
    agent=Agent(backend,retries=0)
    async def run():
        assert (await agent.run('First phase')).output=='Hoi!'
        assert (await agent.run('Second phase')).output=='Hoi!'
        control.settings.disable(identifier)
        with pytest.raises(Exception):await agent.run('Must not dispatch')
    asyncio.run(run());assert len(seen)==2


def test_existing_language_repair_uses_same_provider_with_separate_usage(tmp_path):
    from eva.adapters.objects.identity_verifier import load_current_identity_pack
    from eva.application.conversation import observe_model_requests
    seen=[];completed=[]
    def handler(request):
        body=json.loads(request.content);seen.append(body)
        text='Привет' if len(seen)==1 else 'Hallo Jaap.'
        chunk={'id':'repair-test','object':'chat.completion.chunk','created':1,'model':body['model'],
            'choices':[{'index':0,'delta':{'content':text},'finish_reason':'stop'}]}
        usage={**chunk,'choices':[],'usage':{'prompt_tokens':11,'completion_tokens':2,'total_tokens':13}}
        return httpx2.Response(200,headers={'content-type':'text/event-stream'},text='data: '+json.dumps(chunk)+'\n\ndata: '+json.dumps(usage)+'\n\ndata: [DONE]\n\n')
    baseline,control,identifier=configured(tmp_path,'openai_compatible',handler)
    control.settings.record_check(identifier,ready=True,usage=None);control.settings.select(identifier)
    turn=replace(_turn(),conversation_id='dashboard:repair')
    class Journal:
        def begin_model_request(self,turn):return str(len(seen))
        def complete_model_request(self,turn,request_id,usage,**kwargs):completed.append(usage)
    with observe_model_requests(turn,Journal()):
        result=OptionalConversationModel(baseline,control).generate(turn,
            replace(_identity(),identity_pack=load_current_identity_pack()),_memory(),())
    assert result.text=='Hallo Jaap.' and result.provider=='openai_compatible:test-model'
    assert result.usage.model_attempts==2 and result.usage.input_tokens==22
    assert len(seen)==len(completed)==2


def test_default_and_non_text_routes_do_not_use_optional_provider(tmp_path,monkeypatch):
    from eva.application.conversation import ConversationModelResult
    baseline,control,identifier=configured(tmp_path,'openai_compatible',lambda request:pytest.fail('unexpected network'))
    monkeypatch.setattr(baseline,'generate',lambda *args,**kwargs:ConversationModelResult('Bestaande route','baseline'))
    model=OptionalConversationModel(baseline,control)
    assert model.generate(replace(_turn(),conversation_id='telegram:default')).provider=='baseline'
    control.settings.record_check(identifier,ready=True,usage=None);control.settings.select(identifier)
    assert model.generate(replace(_turn(),conversation_id='voice:unchanged')).provider=='baseline'
    assert model.generate(replace(_turn(),conversation_id='initiative:unchanged')).provider=='baseline'


def test_unchosen_optional_settings_do_not_create_storage_or_block_default(tmp_path):
    from eva.operations.model_provider_settings import ModelProviderSettings
    directory=tmp_path/'not-provisioned'
    assert ModelProviderSettings(directory).read()['selected_id'] is None
    assert not directory.exists()


def test_remote_http_is_rejected_and_missing_usage_is_not_zero_success(tmp_path):
    _,control,identifier=configured(tmp_path,'openai_compatible',lambda request:httpx2.Response(200,json={
        'id':'test','object':'chat.completion','created':1,'model':'test-model',
        'choices':[{'index':0,'message':{'role':'assistant','content':'Missing usage'},'finish_reason':'stop'}]}))
    assert control.check(identifier)['connections'][0]['check_state']=='failed'
    assert control.status()['connections'][0]['check_usage'] is None
    with pytest.raises(ValueError,match='HTTPS'):
        control.settings.configure(kind='openai_compatible',label='Unsafe',model='test',endpoint='http://models.example.test/v1',api_key='synthetic-test-key-not-real')
