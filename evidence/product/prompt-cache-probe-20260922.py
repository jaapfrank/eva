"""Bounded offline wire inspection; live mode is separately explicit (at most four calls).

Uses installed runtime configuration and complete configured tool descriptors,
but synthetic empty memory/history. Never prints or persists prompt/credential bytes.
"""
import asyncio
import copy
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4
import httpx2
import sqlalchemy as sa
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.application.conversation import ConversationTurn, IdentityProfile, RecentConversationMessage
from eva.providers.runtime import build_external_provider_bundle
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.model import pydantic_conversation as conversation
from eva.adapters.model import openai_codex_provider as codex
from eva.contracts.canonical_json import canonical_json_bytes


class Captured(BaseException):
    pass


def digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def construct():
    settings = RuntimeSettings.from_environment()
    pack = load_current_identity_pack(Path(settings.identity_pack_file),
        trust=load_identity_pack_trust_binding(Path(settings.identity_pack_trust_file)))
    identity = IdentityProfile(name=settings.identity_name,description=settings.identity_description,
        principles=("Zeg eerlijk wat je wel en niet weet.",
            "Reageer als één persoon, niet als een verzameling scripts.",
            "Beweer een handeling pas na onafhankelijk bewijs.",
            "Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen."),
        maximum_reply_words=110,identity_pack=pack)
    # An inert engine is enough to construct the configured descriptors. No route
    # method, database statement, recall, effect or provider tool is invoked.
    engine = sa.create_engine('postgresql+psycopg://invalid@127.0.0.1/invalid')
    from eva.adapters.connectors.mcp_gateway import McpGatewayToolAdapter
    descriptors = ()
    for loaded in settings.external_provider.providers:
        if loaded.provider_id == "mcp_gateway":
            descriptors += McpGatewayToolAdapter.conversation_tools(loaded.settings.specs)
        elif loaded.provider_id == "mcp_autonomy":
            from eva.providers.mcp_registration import registration_descriptors
            from eva.providers.mcp_discovery import McpDiscoveryToolAdapter
            descriptors += registration_descriptors() + McpDiscoveryToolAdapter.conversation_tools()
        elif loaded.provider_id == "document_goal_actions":
            from eva.providers.document_goal_actions import DocumentGoalActions
            if getattr(loaded.settings,"pool_file",""):
                from eva.providers.document_task_actions import DocumentTaskActions
                descriptors += DocumentTaskActions.conversation_tools()
            if loaded.settings.plan_file and loaded.settings.definition_file:
                descriptors += DocumentGoalActions.conversation_tools()
        elif loaded.provider_id == "procedure_references":
            from eva.providers.procedure_reference_actions import ProcedureReferenceActions
            descriptors += ProcedureReferenceActions.conversation_tools()
        elif loaded.provider_id == "home_assistant":
            descriptors += loaded.factory.build(loaded.settings,engine).conversation_tools
        else:raise RuntimeError("Unrecognized offline descriptor factory: "+loaded.provider_id)
    descriptors += PostgresDelegatedWorkActions.conversation_tools(include_research=settings.delegated_research_enabled)
    model = conversation.PydanticConversationModel(auth_file=Path(settings.conversation_auth_file),
        model=settings.conversation_contact_model,reasoning_effort=settings.conversation_contact_reasoning_effort,
        resource_budget=settings.runtime_resource_budget,conversation_tools=descriptors)
    actions = tuple(d.name for d in descriptors)
    turn = ConversationTurn.private(settings.background_tenant_id,settings.background_installation_id,
        settings.background_principal_id,'cache-probe:'+str(uuid4()),'probe:hello','Hallo.',
        '2026-09-22T12:00:00Z',project_id='cache-probe:'+str(uuid4()))
    memory = SimpleNamespace(confirmed_facts=(),style_mirror=None)
    captures=[]
    original=conversation.OpenAICodexProvider
    class CaptureTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self,request):
            captures.append(json.loads(request.content))
            raise Captured()
    def capture_provider(settings,**kwargs):
        return original(settings,credential_loader=lambda:('offline-fixture','offline-account'),network_transport=CaptureTransport())
    conversation.OpenAICodexProvider=capture_provider
    try:
        for label in ('A','B'):
            recent=(RecentConversationMessage('user','Dit is een gecontroleerde cacheproef '+label+'.','2026-09-22T12:00:00Z'),)
            try:model.generate(turn,identity,memory,recent,available_actions=actions)
            except Captured:pass
    finally:
        conversation.OpenAICodexProvider=original
        engine.dispose()
    assert len(captures)==2
    return model,captures


def candidate(body):
    result=copy.deepcopy(body)
    original=body['instructions']
    boundary=original.index('Duurzame stijlspiegel:\n')
    first=original.index('\n\n')+2
    root,prefix,suffix=original[:first],original[first:boundary],original[boundary:]
    assert root+prefix+suffix==original
    result['instructions']=root
    result['input']=[dict(type='message',role='developer',content=[
        dict(type='input_text',text=prefix,prompt_cache_breakpoint={'mode':'explicit'}),
        dict(type='input_text',text=suffix)])]+copy.deepcopy(body['input'])
    assert result['tools']==body['tools']
    assert {k:v for k,v in result.items() if k not in {'instructions','input'}} == {
        k:v for k,v in body.items() if k not in {'instructions','input'}}
    return result


def measure(bodies):
    tokenizer=conversation._input_tokenizer()
    def count(value):return len(tokenizer.encode(value if isinstance(value,str) else canonical_json_bytes(value).decode()))
    rows=[]
    for b in bodies:
        instructions=b['instructions'];boundary=instructions.index('Duurzame stijlspiegel:\n')
        rows.append(dict(wire_bytes=len(canonical_json_bytes(b)),tools=len(b['tools']),
            local_serialized_tokens=count(b),tools_serialized_tokens=count(b['tools']),
            instructions_tokens=count(instructions),stable_instruction_prefix_tokens=count(instructions[:boundary]),
            changing_instruction_suffix_tokens=count(instructions[boundary:]),input_tokens=count(b['input']),
            tools_sha256=digest(b['tools']),stable_prefix_sha256=digest(instructions[:boundary]),
            body_sha256=digest(b)))
    support={}
    from openai.resources.responses import AsyncResponses
    from openai.types.responses import ResponseInputTextParam
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings
    support['sdk_options']='prompt_cache_options' in inspect.signature(AsyncResponses.create).parameters
    support['sdk_block_marker']='prompt_cache_breakpoint' in ResponseInputTextParam.__annotations__
    support['pydantic_options']='openai_prompt_cache_options' in OpenAIResponsesModelSettings.__annotations__
    try:
        codex._codex_compatible_request(httpx2.Request('POST',codex.CODEX_SUBSCRIPTION_ENDPOINT if hasattr(codex,'CODEX_SUBSCRIPTION_ENDPOINT') else 'https://chatgpt.com/backend-api/codex/responses',json=candidate(bodies[0])))
    except codex.CodexProviderError:
        support['current_projector_accepts_candidate']=False
    else:support['current_projector_accepts_candidate']=True
    return dict(scope='Configured identity and external/delegated catalog, synthetic empty memory and controlled history; not a captured owner conversation.',
        versions={k:importlib.metadata.version(k) for k in ('openai','pydantic-ai-slim','httpx2')},
        model=bodies[0]['model'],reasoning=bodies[0].get('reasoning'),support=support,
        rows=rows,all_instruction_characters_preserved=True,all_tools_preserved=True,
        cache_key_stable=bodies[0]['prompt_cache_key']==bodies[1]['prompt_cache_key'],
        model_calls=0,local_token_estimates_are_not_provider_usage=True)


async def live_pair_probe(model,bodies):
    """Four one-shot SDK requests, no retries or tool dispatch, no prompt logging."""
    global_network_count=0
    records=[]
    approved={digest(body) for body in bodies+[candidate(body) for body in bodies]}
    original_projector=codex._codex_compatible_request
    def experiment_projector(request):
        # Only four exact offline-checked packets can bypass the old projection.
        body=json.loads(request.content)
        if digest(body) not in approved:
            raise codex.CodexProviderError('configuration')
        return request
    codex._codex_compatible_request=experiment_projector
    try:
        for label,body in [('baseline_first',bodies[0]),('baseline_second',bodies[1]),
                           ('candidate_first',candidate(bodies[0])),('candidate_second',candidate(bodies[1]))]:
            started=monotonic()
            record={'case':label,'wire_sha256':digest(body),'raw_usage':None,'first_text_ms':None,
                'text_characters':0,'function_calls':0,'executed_tools':0}
            class MeteredBody(httpx2.AsyncByteStream):
                def __init__(self,inner):self.inner=inner;self.pending=b''
                async def __aiter__(self):
                    async for chunk in self.inner:
                        self.pending+=chunk
                        while b'\n' in self.pending:
                            line,self.pending=self.pending.split(b'\n',1)
                            if line.startswith(b'data: '):
                                try:event=json.loads(line[6:])
                                except (ValueError,UnicodeDecodeError):continue
                                if event.get('type') in {'response.completed','response.incomplete','response.failed'}:
                                    record['raw_usage']=event.get('response',{}).get('usage')
                                    record['terminal_type']=event.get('type')
                                if event.get('type')=='response.output_text.delta':
                                    if record['first_text_ms'] is None:record['first_text_ms']=round((monotonic()-started)*1000)
                                    record['text_characters']+=len(event.get('delta',''))
                                if event.get('type')=='response.output_item.added' and event.get('item',{}).get('type')=='function_call':
                                    record['function_calls']+=1
                        yield chunk
                async def aclose(self):await self.inner.aclose()
            class MeteredNetwork(httpx2.AsyncBaseTransport):
                def __init__(self):self.network=httpx2.AsyncHTTPTransport(retries=0)
                async def handle_async_request(self,request):
                    nonlocal global_network_count
                    if str(request.url)!=model._endpoint or global_network_count>=4:
                        raise codex.CodexProviderError('configuration')
                    global_network_count+=1
                    response=await self.network.handle_async_request(request)
                    record['http_status']=response.status_code
                    if response.status_code>=400:
                        payload=await response.aread()
                        try:
                            error=json.loads(payload).get('error',{})
                            if isinstance(error,str):error={'message':error}
                            record['error_type']=error.get('type')
                            record['error_code']=error.get('code')
                            record['error_param']=error.get('param')
                            message=str(error.get('message',''))
                            record['error_mentions']=[x for x in ['prompt_cache_breakpoint','prompt_cache_options','developer','instructions'] if x in message]
                            record['error_body_sha256']=hashlib.sha256(payload).hexdigest()
                        except (ValueError,AttributeError):record['error_parse']='unavailable'
                    response.stream=MeteredBody(response.stream)
                    return response
                async def aclose(self):await self.network.aclose()
            settings=codex.OpenAICodexProviderSettings(endpoint=model._endpoint,model_name=model._model,
                reasoning_effort=model._reasoning_effort,timeout_seconds=60,
                maximum_request_bytes=conversation._MAX_STREAM_BYTES,maximum_response_bytes=conversation._MAX_STREAM_BYTES)
            provider=codex.OpenAICodexProvider(settings,credential_loader=model._load_auth,network_transport=MeteredNetwork())
            try:
                async with provider as session:
                    stream=await provider._sdk_client.responses.create(**body)
                    async with stream:
                        async for event in stream:pass
                    raw=session.consume_raw_usage()
                    record['attested_input_tokens']=raw.input_tokens
                    record['attested_output_tokens']=raw.output_tokens
                    assert session.model_requests==1
                record['status']='completed'
            except Exception as error:
                record['status']='failed'
                record['exception_type']=type(error).__name__
            record['wall_ms']=round((monotonic()-started)*1000)
            records.append(record)
            print(json.dumps(record),flush=True)
            if record['status']!='completed':break
    finally:codex._codex_compatible_request=original_projector
    print(json.dumps({'actual_http_model_requests':global_network_count,'executed_tools':0,
        'total_measured_input_tokens':sum((r['raw_usage'] or {}).get('input_tokens',0) for r in records),
        'total_measured_output_tokens':sum((r['raw_usage'] or {}).get('output_tokens',0) for r in records),
        'unmeasured_requests':sum(r['raw_usage'] is None for r in records),
        'actual_subscription_cost':'unknown','general_speed_claim':False}),flush=True)


if __name__=='__main__':
    import sys
    if sys.argv[1:] not in ([],['--live-four-calls']):raise SystemExit('Unknown probe mode')
    model,bodies=construct()
    result=measure(bodies)
    print(json.dumps(result),flush=True)
    if sys.argv[1:]==['--live-four-calls']:
        asyncio.run(live_pair_probe(model,bodies))
