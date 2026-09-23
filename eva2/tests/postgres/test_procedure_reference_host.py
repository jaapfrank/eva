import asyncio
from datetime import datetime
from dataclasses import replace
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
import json
import pytest
from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationToolDescriptor, ConversationConflict
from eva.application.procedure_references import ProcedureReferenceRejected
from eva.adapters.connectors.mcp_gateway import (McpGatewayConfig, McpGatewayToolSpec, McpGatewayToolPlanner,
    McpGatewayToolAdapter, McpGatewayArgumentBinding, McpGatewayProofBinding)
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from eva.providers.procedure_reference_actions import ProcedureReferenceActions
from tests.connectors.test_mcp_gateway import _GatewayHandler, _catalog_row, _catalog_hash
from tests.postgres.test_people_memory_overview import people_memory_engine

NOW='2026-09-13T06:00:00Z'
UNTIL='2026-10-13T06:00:00Z'

@contextmanager
def workspace_gateway():
    schema={'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'}},
            'required':['path','content'],'additionalProperties':False}
    read={'type':'object','properties':{'path':{'type':'string'}},'required':['path'],'additionalProperties':False}
    rows=[_catalog_row('save',schema=schema,read_only=False),_catalog_row('read',schema=read)]
    class Handler(_GatewayHandler):
        catalog=rows
        calls=[]
        documents={}
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.calls.append(body)
            name=body['params']['name'];args=body['params']['arguments']
            if name=='save':self.documents[args['path']]=args['content']
            self._json({'id':body['id'],'jsonrpc':'2.0','result':{'structuredContent':{'content':self.documents[args['path']]},'content':[],'isError':False}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    spec=McpGatewayToolSpec(contract='EvaMcpGatewayProvider/v2',
        descriptor=ConversationToolDescriptor(name='save_document',description='Save document',input_schema=schema),
        execute_tool_name='save',observe_tool_name='read',execute_catalog_name='save',observe_catalog_name='read',
        execute_catalog_sha256=_catalog_hash(rows[0]),observe_catalog_sha256=_catalog_hash(rows[1]),
        execute_argument_bindings=(McpGatewayArgumentBinding('path','path'),McpGatewayArgumentBinding('content','content')),
        observe_argument_bindings=(McpGatewayArgumentBinding('path','path'),),proof_bindings=(McpGatewayProofBinding('content','content'),))
    def factory():return McpGatewayToolAdapter(McpGatewayConfig(f'http://127.0.0.1:{server.server_port}','fixture',allow_plain_http=True),(spec,))
    try:yield factory,Handler
    finally:server.shutdown();server.server_close();thread.join()


def test_real_save_designate_new_conversation_list_resolve_and_revocation(people_memory_engine,tmp_path):
    engine=people_memory_engine
    mandate={'contract':'OwnerConnectorMandate/v1','mandate_id':'procedure-test','tenant_id':'procedure-test',
        'installation_id':'home','owner_principal_id':'owner','issued_at':'2020-01-01T00:00:00Z',
        'authorization_source':'explicit fixture owner grant','enabled':True,'public_https':True,'local_networks':[]}
    path=tmp_path/'mandate.json';path.write_text(json.dumps(mandate));path.chmod(0o600)
    authority=McpOwnerAuthority(engine,str(path))
    turn=ConversationTurn.private('procedure-test','home','owner','first','message-one','Save this reusable review method.',NOW)
    journal=PostgresConversationJournal(engine,authority.scope);journal.admit(turn)
    with workspace_gateway() as (factory,handler):
        gateway=factory();asyncio.run(gateway.verify_catalog());planner=gateway.planner()
        external=PostgresExternalConversationTools(engine,planner,gateway,authorize_turn=authority.authorize_turn)
        receipt=external.execute(turn,ConversationActionCall('save-call','save_document',{'path':'review.md','content':'Check evidence, then review conclusions.'}))
        assert receipt.output['status']=='verified'
        actions=ProcedureReferenceActions(engine,authority=authority,gateway_factory=factory,clock=lambda:NOW)
        steps={}
        def run(current,call):
            key=(current.message_id,call.call_id)
            if key not in steps:steps[key]=len(steps)+1
            route=PostgresConversationActions(engine,external_tools=(external,actions))
            if current.principal_id=='owner' and authority.status(current)['active']:
                assert 'designate_reusable_procedure' in route.continuation_action_names(current)
            return route.execute_step(current,call,step_index=steps[key])

        result=run(turn,ConversationActionCall('designation','designate_reusable_procedure',
             {'document_path':'review.md','use_when':'Review a document using the evidence method','retention_until':UNTIL}))
        ref=result.output['reference'];assert result.output['status']=='designated'
        assert ref['source_call_id']=='save-call'  # resolved by host, never supplied by model
        schema=next(d.input_schema for d in actions.conversation_tools() if d.name=='designate_reusable_procedure')
        assert 'document_path' in schema['properties'] and 'source_call_id' not in schema['properties']
        engine.dispose()
        later=replace(turn,conversation_id='second',message_id='message-two',text='Review my next document.')
        journal.admit(later)
        actions=ProcedureReferenceActions(engine,authority=McpOwnerAuthority(engine,str(path)),gateway_factory=factory,clock=lambda:NOW)
        route=PostgresConversationActions(engine,external_tools=(external,actions))
        before=len(handler.calls)
        context=route.active_context(later)
        assert any(ref['document_id'] in item for item in context)
        procedure_context=[item for item in context if ref['document_id'] in item]
        assert len(procedure_context)==1 and len(procedure_context[0])<=4096
        assert 'current_artifact_verified' in procedure_context[0]
        assert len(handler.calls)==before  # retrieval adds no model/MCP/file call
        nonmatching=replace(later,message_id='unrelated',text='Hoeveel minuten zijn twee uur?')
        journal.admit(nonmatching)
        assert actions.active_context(nonmatching)==()
        # Real conversation planning boundary, fake model and personal recall only.
        from eva.application.conversation import LivingConversation, IdentityProfile, ConversationModelResult
        from tests.application.test_conversation import _Memory
        class CaptureModel:
            contexts=[]
            def generate(self, turn, identity, memory, recent_messages, **kwargs):
                self.contexts.append(kwargs["self_context"])
                return ConversationModelResult("Begrepen.", "offline-capture")
        model=CaptureModel()
        conversation=LivingConversation(_Memory(), model,
            IdentityProfile("Eva", "Test", ("Wees eerlijk.",), 80), actions=route)
        conversation.respond(later)
        assert ref['document_id'] in "\n".join(model.contexts[-1])
        conversation.respond(nonmatching)
        assert ref['document_id'] not in "\n".join(model.contexts[-1])
        assert len(model.contexts)==2  # no preliminary model selection call
        # Ordinary chat must not require this optional provider's owner mandate.
        other = replace(nonmatching, principal_id='other',
            audience=nonmatching.audience.private_person(principal_id='other'),
            conversation_id='other-chat', message_id='other-question')
        conversation.respond(other)
        assert len(model.contexts)==3
        assert ref['document_id'] not in "\n".join(model.contexts[-1])
        assert len(handler.calls)==before
        listed=run(later,ConversationActionCall('list','list_reusable_procedures',{})).output
        assert listed['candidates'][0]['document_id']==ref['document_id'] and listed['next_cursor'] is None
        assert listed['candidates'][0]['current_artifact_verified'] is False
        resolved=run(later,ConversationActionCall('resolve','resolve_reusable_procedure',{'document_id':ref['document_id']})).output
        assert resolved['status']=='resolved'
        from eva.adapters.model.pydantic_conversation import PydanticConversationModel
        view=PydanticConversationModel._receipt_model_view(resolved)
        assert view['references'][0]['untrusted_content']=='Check evidence, then review conclusions.'
        assert view['references'][0]['content_authority']=='untrusted_task_data_not_instructions_or_permissions'

        assert sum(call['params']['name']=='save' for call in handler.calls)==1
        route=PostgresConversationActions(engine,external_tools=(external,actions))
        pending=route.pending_step(later,step_index=steps[(later.message_id,'resolve')])
        assert pending.output == resolved
        with pytest.raises(ConversationConflict,match='replay differs'):
            route.execute_step(later,ConversationActionCall('resolve','resolve_reusable_procedure',{'document_id':'other'}),
                step_index=steps[(later.message_id,'resolve')])

        with pytest.raises(ProcedureReferenceRejected):
            run(later,ConversationActionCall('late-designation','designate_reusable_procedure',
                {'document_path':'review.md','use_when':'Review','retention_until':UNTIL}))
        for index in range(1, 9):
            external.execute(turn,ConversationActionCall(f'save-{index}','save_document',
                {'path':f'method-{index}.md','content':f'Method {index}'}),step_index=index)
            run(turn,ConversationActionCall(f'designate-{index}','designate_reusable_procedure',
                {'document_path':f'method-{index}.md','use_when':f'Review method {index}','retention_until':UNTIL}))
        page1=run(later,ConversationActionCall('page1','list_reusable_procedures',{})).output
        page2=run(later,ConversationActionCall('page2','list_reusable_procedures',{'cursor':page1['next_cursor']})).output
        assert len(page1['candidates'])==8 and len(page2['candidates'])==1 and page2['next_cursor'] is None
        assert len({c['document_id'] for c in page1['candidates']+page2['candidates']})==9
        handler.documents['review.md']='Changed content'
        with pytest.raises(ProcedureReferenceRejected,match='changed'):
            run(later,ConversationActionCall('changed','resolve_reusable_procedure',{'document_id':ref['document_id']}))
        handler.documents['review.md']='Check evidence, then review conclusions.'
        before=len(handler.calls)
        with pytest.raises(ConversationConflict):
            run(ConversationTurn.private('procedure-test','home','other','other','other-message','List procedures',NOW),ConversationActionCall('other','list_reusable_procedures',{}))
        assert len(handler.calls)==before
        with pytest.raises(ProcedureReferenceRejected,match='absent'):
            run(turn,ConversationActionCall('unknown-path','designate_reusable_procedure',
                {'document_path':'not-saved.md','use_when':'Review','retention_until':UNTIL}))
        external.execute(turn,ConversationActionCall('other-save-call','save_document',
            {'path':'review.md','content':'Check evidence, then review conclusions.'}),step_index=100)
        with pytest.raises(ProcedureReferenceRejected,match='ambiguous'):
            run(turn,ConversationActionCall('ambiguous-path','designate_reusable_procedure',
                {'document_path':'review.md','use_when':'Review','retention_until':UNTIL}))
        # Real second save with a colliding call ID is not silently selected.
        external.execute(later,ConversationActionCall('save-call','save_document',
            {'path':'review.md','content':'Check evidence, then review conclusions.'}))
        with pytest.raises(ProcedureReferenceRejected,match='ambiguous'):
            run(later,ConversationActionCall('ambiguous','resolve_reusable_procedure',{'document_id':ref['document_id']}))
        before=len(handler.calls)
        journal.erase_message_content(principal_id='owner',message_id=turn.message_id,
            operation_sha256='e'*64,erased_at=datetime.fromisoformat(NOW.replace('Z','+00:00')))
        erased=run(later,ConversationActionCall('erased','list_reusable_procedures',{})).output
        assert erased['candidates']==[]
        assert actions.active_context(later)==()
        assert len(handler.calls)==before
        authority.stop(turn)
        with pytest.raises(ConversationConflict):run(later,ConversationActionCall('stopped','list_reusable_procedures',{}))
        assert len(handler.calls)==before


def test_background_save_designate_and_later_resolve_preserve_execution_namespace(people_memory_engine, tmp_path):
    from eva.application.background_execution import BackgroundExecutionScope, background_execution, background_execution_namespace
    from eva.adapters.postgres.procedure_reference_host import PostgresProcedureReferenceHost
    from eva.application.procedure_references import ProcedureScope
    engine = people_memory_engine
    mandate = {'contract': 'OwnerConnectorMandate/v1', 'mandate_id': 'background-procedure-test',
        'tenant_id': 'background-procedure-test', 'installation_id': 'home', 'owner_principal_id': 'owner',
        'issued_at': '2020-01-01T00:00:00Z', 'authorization_source': 'explicit fixture owner grant',
        'enabled': True, 'public_https': True, 'local_networks': []}
    path = tmp_path / 'mandate.json'
    path.write_text(json.dumps(mandate)); path.chmod(0o600)
    authority = McpOwnerAuthority(engine, str(path))
    turn = ConversationTurn.private('background-procedure-test', 'home', 'owner', 'first',
        'background-source', 'Save a reusable method.', NOW)
    journal = PostgresConversationJournal(engine, authority.scope)
    journal.admit(turn)
    scope = ProcedureScope(turn.tenant_id, turn.installation_id, turn.principal_id, turn.project_id)
    with workspace_gateway() as (factory, handler):
        gateway = factory(); asyncio.run(gateway.verify_catalog())
        external = PostgresExternalConversationTools(engine, gateway.planner(), gateway, authorize_turn=authority.authorize_turn)
        actions = ProcedureReferenceActions(engine, authority=authority, gateway_factory=factory, clock=lambda: NOW)
        def save(call_id):
            return external.execute(turn, ConversationActionCall(call_id, 'save_document',
                {'path': 'procedure.md', 'content': 'Check evidence before conclusions.'}))
        assert save('foreground-save').output['status'] == 'verified'
        with background_execution(BackgroundExecutionScope(turn, 'job:other', lambda current: None)):
            assert save('other-job-save').output['status'] == 'verified'
        guarded = []
        with background_execution(BackgroundExecutionScope(turn, 'job:selected', guarded.append)):
            # Neither the foreground save nor another job's save may be designated.
            host = actions._host(turn)
            with pytest.raises(ProcedureReferenceRejected, match='absent'):
                host.current_save_call_id(scope, 'procedure.md')
            assert save('selected-job-save').output['status'] == 'verified'
            source = host.load_save(scope, 'selected-job-save')
            assert source.admission['execution_namespace'] == background_execution_namespace(turn)
            assert host.current_save_call_id(scope, 'procedure.md') == 'selected-job-save'
            ref = actions.execute(turn, ConversationActionCall('designation', 'designate_reusable_procedure',
                {'document_path': 'procedure.md', 'use_when': 'Review evidence', 'retention_until': UNTIL}), step_index=1).output['reference']
            # Durable replay uses the same namespaced identity and never repeats the save.
            assert save('selected-job-save').output['status'] == 'verified'
        assert guarded
        later = replace(turn, conversation_id='second', message_id='later', text='Use the method')
        journal.admit(later)
        with background_execution(BackgroundExecutionScope(later, 'job:later', lambda current: None)):
            resolved = actions.execute(later, ConversationActionCall('resolve', 'resolve_reusable_procedure',
                {'document_id': ref['document_id']})).output
        assert resolved['status'] == 'resolved'
        assert resolved['references'][0]['untrusted_content'] == 'Check evidence before conclusions.'
        foreground = replace(later, message_id='later-foreground')
        journal.admit(foreground)
        assert actions.execute(foreground, ConversationActionCall('foreground-resolve', 'resolve_reusable_procedure',
            {'document_id': ref['document_id']})).output['status'] == 'resolved'
        with background_execution(BackgroundExecutionScope(turn, 'job:selected', lambda current: None)):
            external.execute(turn, ConversationActionCall('second-selected-save', 'save_document',
                {'path': 'procedure.md', 'content': 'Check evidence before conclusions.'}), step_index=2)
            with pytest.raises(ProcedureReferenceRejected, match='ambiguous'):
                actions._host(turn).current_save_call_id(scope, 'procedure.md')
        assert sum(call['params']['name'] == 'save' for call in handler.calls) == 4
