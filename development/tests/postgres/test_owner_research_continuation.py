"""Scripted choices prove wiring and durable recovery, not autonomous model quality."""
import json

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.application.conversation import (ConversationActionCall, ConversationConflict,
    ConversationModelUsage, ConversationToolDescriptor, ConversationToolPreparation,
    ConversationTurn, IdentityProfile, LivingConversation)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.application.research import ResearchArtifact, ResearchSource
from tests.postgres.test_operational_timeline import timeline_engine
from tests.support.scripted_pydantic_conversation import (
    ScriptedCall, ScriptedPhase, ScriptedPydanticConversation, tool_returns)

RESEARCH = 'research_current_options'
URL = 'https://docs.example/specification'
ARGS = {'question':'Welke grenzen noemt de primaire specificatie?', 'title':'Broncontrole',
    'options':[URL], 'research_mode':'topic', 'public_review':'not_applicable'}
USAGE = ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=39122, output_tokens=1267)


class Research:
    def __init__(self, fail=False): self.calls, self.fail = 0, fail
    def native_research_timeout_seconds(self): return 55
    def research_configuration_sha256(self): return 'b'*64
    def research_request_payload(self, *, question, options, research_mode='comparison'):
        return {'question':question, 'options':list(options), 'mode':research_mode}
    def research_current_options(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise ConversationModelError('measured output budget exceeded', model_usage=USAGE)
        return ResearchArtifact(kwargs['title'], kwargs['question'], 'PRIMARY_SOURCE_FINDING',
            (ResearchSource('Primaire specificatie',URL),), kwargs['researched_at'], 'fixture',
            usage=ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=500,output_tokens=100))


class Connector:
    action_names = ('fetch_source','save_finding')
    def __init__(self): self.calls = []
    async def prepare(self, turn, call):
        return ConversationToolPreparation(call.name, call.arguments, {}, {}, observation_output_key='result')
    async def execute(self, request):
        self.calls.append(request.tool_name)
        return ToolDispatchResult(True,request.operation_id,{'text':
            'Content could not be retrieved' if request.tool_name=='fetch_source' else request.arguments['content']})
    async def observe(self, request, dispatch):
        return ToolProofObservation('verified',{}, {'observation':dispatch.details,'external_state_verified':False})


class Memory:
    def recall_for_presence(self, query): return HindsightMemoryEngine._empty_presence(query)


def setup(engine, label, fail=False):
    turn=ConversationTurn.private('owner-research-'+label,'home','owner','chat','message',
        'Onderzoek de primaire bron en bewaar de bevinding.', '2026-09-12T20:00:00Z')
    journal=PostgresConversationJournal(engine,external_timeline_scope(turn.tenant_id,turn.installation_id,'scope'))
    journal.admit(turn)
    research,connector=Research(fail),Connector()
    tools=PostgresExternalConversationTools(engine,connector,connector)
    actions=PostgresConversationActions(engine,research=research,external_tools=(tools,))
    return turn,journal,research,connector,tools,actions


def test_scripted_fetch_failure_can_research_then_save_and_recover(timeline_engine,tmp_path,monkeypatch):
    turn,journal,research,connector,tools,actions=setup(timeline_engine,'followthrough')
    script=ScriptedPydanticConversation((
        ScriptedPhase.deferred(ScriptedCall('fetch','fetch_source',{'content':URL})),
        ScriptedPhase.deferred(ScriptedCall('research',RESEARCH,ARGS)),
        ScriptedPhase.deferred(ScriptedCall('save','save_finding',{'content':'PRIMARY_SOURCE_FINDING'})),
        ScriptedPhase.text('De bronbevinding is bewaard.'),
        ScriptedPhase.text('De bronbevinding is bewaard.'),
    ))
    schema={'type':'object','properties':{'content':{'type':'string'}},'required':['content'],'additionalProperties':False}
    model=PydanticConversationModel(auth_file=tmp_path/'unused',model_backend=script.model,
        conversation_tools=tuple(ConversationToolDescriptor(name,'Fixture bronactie',schema,
            'Een providerantwoord bewijst alleen wat daarin staat.') for name in Connector.action_names))
    catalogs=[]
    run_phase=model._run_pydantic_phase
    def capture_phase(**kwargs):
        catalogs.append(kwargs['tools'])
        return run_phase(**kwargs)
    monkeypatch.setattr(model,'_run_pydantic_phase',capture_phase)
    def conversation(current_actions):
        return LivingConversation(Memory(),model,IdentityProfile('Eva','Huisgenoot',('Eerlijk',),80),current_actions)
    assert conversation(actions).respond(turn,model_journal=journal).text=='De bronbevinding is bewaard.'
    assert RESEARCH in [tool['name'] for tool in catalogs[1]]
    assert 'Content could not be retrieved' in json.dumps([p.content for p in tool_returns(script.captures[1].messages)])
    assert 'PRIMARY_SOURCE_FINDING' in json.dumps([p.content for p in tool_returns(script.captures[2].messages)])
    receipt=actions.pending_step(turn,step_index=1)
    assert receipt.output['result']['sources'][0]['url']==URL
    assert receipt.output['result']['model_usage']['output_tokens']==100
    restarted=PostgresConversationActions(timeline_engine,research=research,external_tools=(tools,))
    assert conversation(restarted).respond(turn,model_journal=journal).text=='De bronbevinding is bewaard.'
    assert research.calls==1 and connector.calls==['fetch_source','save_finding']
    assert not script.remaining_phases
    with pytest.raises(ConversationConflict):
        restarted.execute_step(turn,ConversationActionCall('different','save_finding',{'content':'different'}),step_index=1)
    with pytest.raises(ConversationConflict):
        restarted.execute_step(turn,ConversationActionCall('changed',RESEARCH,{**ARGS,'question':'Other question'}),step_index=1)
    events=restarted._owner_research_steps._timeline(turn).events(
        restarted._owner_research_steps._thread_id(turn,RESEARCH,1))
    other=ConversationTurn.private(turn.tenant_id,turn.installation_id,'other',
        turn.conversation_id,turn.message_id,turn.text,turn.occurred_at)
    with pytest.raises(ConversationConflict,match='scope'):
        restarted._owner_research_steps._receipt(other,events,1)
    assert research.calls==1


def test_research_budget_failure_is_durable_and_not_retried(timeline_engine,tmp_path):
    turn,journal,research,connector,tools,actions=setup(timeline_engine,'budget',True)
    actions.execute(turn,ConversationActionCall('fetch','fetch_source',{'content':URL}))
    call=ConversationActionCall('research',RESEARCH,ARGS)
    try:
        receipt=actions.execute_step(turn,call,step_index=1)
    except ConversationModelError:
        receipt=actions.pending_step(turn,step_index=1)
    assert receipt.output['status']=='failed'
    assert actions.pending_step(turn,step_index=1)==receipt
    assert actions.execute_step(turn,call,step_index=1)==receipt
    assert research.calls==1
    timeline=actions._owner_research_steps._timeline(turn)
    events=timeline.events(actions._owner_research_steps._thread_id(turn,RESEARCH,1))
    usages=[e.payload['data']['model_usage'] for e in events if 'model_usage' in e.payload['data']]
    assert usages==[USAGE.to_wire()]
    script=ScriptedPydanticConversation((ScriptedPhase.text('Het onderzoek is niet afgerond.'),))
    model=PydanticConversationModel(auth_file=tmp_path/'unused',model_backend=script.model)
    conversation=LivingConversation(Memory(),model,IdentityProfile('Eva','Huisgenoot',('Eerlijk',),80),actions)
    assert conversation.respond(turn,model_journal=journal).text=='Het onderzoek is niet afgerond.'
    assert 'geen geverifieerd onderzoeksdocument' in script.captures[0].instructions
    assert 'bevat een werkelijk uitgevoerd webonderzoek' not in script.captures[0].instructions
    assert not script.captures[0].native_tool_kinds and research.calls==1


def test_owner_continuation_rejects_initiative_review_without_dispatch(timeline_engine):
    turn,journal,research,connector,tools,actions=setup(timeline_engine,'review')
    assert RESEARCH in actions.continuation_action_names(turn)
    with pytest.raises(ConversationConflict):
        actions.execute_step(turn,ConversationActionCall('review',RESEARCH,
            {**ARGS,'public_review':'public_unlinked'}),step_index=1)
    assert research.calls==0
    assert RESEARCH not in PostgresConversationActions(timeline_engine).continuation_action_names(turn)
    legacy=PostgresConversationActions(timeline_engine,research=object())
    assert RESEARCH in legacy.available_action_names(turn)
    assert RESEARCH not in legacy.continuation_action_names(turn)


@pytest.mark.parametrize('reason',['budget exhausted','owner stop requested'])
def test_before_step_denial_prevents_research_after_failed_fetch(timeline_engine,tmp_path,reason):
    turn,journal,research,connector,tools,actions=setup(timeline_engine,reason.replace(' ','-'))
    script=ScriptedPydanticConversation((
        ScriptedPhase.deferred(ScriptedCall('fetch','fetch_source',{'content':URL})),
        ScriptedPhase.deferred(ScriptedCall('research',RESEARCH,ARGS)),
    ))
    schema={'type':'object','properties':{'content':{'type':'string'}},'required':['content'],'additionalProperties':False}
    model=PydanticConversationModel(auth_file=tmp_path/'unused',model_backend=script.model,
        conversation_tools=tuple(ConversationToolDescriptor(name,'Fixture bronactie',schema)
            for name in Connector.action_names))
    def guard(turn,usage):
        if connector.calls:
            raise ConversationConflict(reason)
    conversation=LivingConversation(Memory(),model,IdentityProfile('Eva','Huisgenoot',('Eerlijk',),80),
        actions,before_step=guard)
    with pytest.raises(ConversationConflict,match=reason):
        conversation.respond(turn,model_journal=journal)
    assert connector.calls==['fetch_source'] and research.calls==0
    assert script.remaining_phases==1


def test_durable_conversation_stop_blocks_indexed_research_dispatch(timeline_engine):
    from eva.application.conversation import ConversationStopped
    turn,journal,research,connector,tools,actions=setup(timeline_engine,'durable-stop')
    actions.execute(turn,ConversationActionCall('fetch','fetch_source',{'content':URL}))
    assert journal.request_conversation_stop(principal_id=turn.principal_id,
        conversation_id=turn.conversation_id)==1
    with pytest.raises(ConversationStopped):
        actions.execute_step(turn,ConversationActionCall('research',RESEARCH,ARGS),step_index=1)
    assert research.calls==0
