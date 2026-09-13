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
             {'source_call_id':'save-call','use_when':'Review a document using the evidence method','retention_until':UNTIL}))
        ref=result.output['reference'];assert result.output['status']=='designated'
        engine.dispose()
        later=replace(turn,conversation_id='second',message_id='message-two',text='Review my next document.')
        journal.admit(later)
        actions=ProcedureReferenceActions(engine,authority=McpOwnerAuthority(engine,str(path)),gateway_factory=factory,clock=lambda:NOW)
        listed=run(later,ConversationActionCall('list','list_reusable_procedures',{})).output
        assert listed['candidates'][0]['document_id']==ref['document_id'] and listed['next_cursor'] is None
        assert listed['candidates'][0]['current_artifact_verified'] is False
        resolved=run(later,ConversationActionCall('resolve','resolve_reusable_procedure',{'document_id':ref['document_id']})).output
        assert resolved['status']=='resolved'
        assert sum(call['params']['name']=='save' for call in handler.calls)==1
        route=PostgresConversationActions(engine,external_tools=(external,actions))
        pending=route.pending_step(later,step_index=steps[(later.message_id,'resolve')])
        assert pending.output == resolved
        with pytest.raises(ConversationConflict,match='replay differs'):
            route.execute_step(later,ConversationActionCall('resolve','resolve_reusable_procedure',{'document_id':'other'}),
                step_index=steps[(later.message_id,'resolve')])

        with pytest.raises(ConversationConflict):
            run(later,ConversationActionCall('late-designation','designate_reusable_procedure',
                {'source_call_id':'save-call','use_when':'Review','retention_until':UNTIL}))
        for index in range(1, 9):
            external.execute(turn,ConversationActionCall(f'save-{index}','save_document',
                {'path':f'method-{index}.md','content':f'Method {index}'}),step_index=index)
            run(turn,ConversationActionCall(f'designate-{index}','designate_reusable_procedure',
                {'source_call_id':f'save-{index}','use_when':f'Review method {index}','retention_until':UNTIL}))
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
        assert len(handler.calls)==before
        authority.stop(turn)
        with pytest.raises(ConversationConflict):run(later,ConversationActionCall('stopped','list_reusable_procedures',{}))
        assert len(handler.calls)==before
