import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import pytest
from eva.application.conversation import ConversationActionCall,ConversationTurn,ConversationConflict
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.providers.procedure_reference_actions import ProcedureReferenceActions
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from tests.postgres.test_procedure_reference_host import workspace_gateway,NOW,UNTIL
from tests.postgres.test_people_memory_overview import people_memory_engine
from tests.application.test_skill_packages import package
from eva.application.skill_packages import SkillResource
from eva.operations.procedure_execution import ProcedureExecutor,ProcedureExecutionConfig


def test_real_save_package_new_turn_resource_execution_and_durable_replay(people_memory_engine,tmp_path):
    engine=people_memory_engine
    mandate={'contract':'OwnerConnectorMandate/v1','mandate_id':'package-test','tenant_id':'package-test',
        'installation_id':'home','owner_principal_id':'owner','issued_at':'2020-01-01T00:00:00Z',
        'authorization_source':'explicit isolated test grant','enabled':True,'public_https':True,'local_networks':[]}
    path=tmp_path/'mandate.json';path.write_text(json.dumps(mandate));path.chmod(0o600)
    authority=McpOwnerAuthority(engine,str(path))
    turn=ConversationTurn.private('package-test','home','owner','first','package-one','Save a reusable skill.',NOW)
    journal=PostgresConversationJournal(engine,authority.scope);journal.admit(turn)
    current=package();script=b"from pathlib import Path\nprint(Path('input.txt').read_text().upper())\nPath('output.txt').write_text(Path('input.txt').read_text().upper())\n"
    current=replace(current,files=tuple(SkillResource(f.path,script) if f.path=='scripts/helper.py' else f for f in current.files)+(SkillResource('references/large.md', b'large retained package body '*1500),))
    class CountingExecutor:
        count=0
        def execute(self,source,input_data):
            self.count+=1
            return ProcedureExecutor(ProcedureExecutionConfig(os.environ['EVA_SKILL_TEST_IMAGE'])).execute(source,input_data)
    executor=CountingExecutor()
    with workspace_gateway() as (factory,handler):
        gateway=factory();asyncio.run(gateway.verify_catalog())
        external=PostgresExternalConversationTools(engine,gateway.planner(),gateway,authorize_turn=authority.authorize_turn)
        saved=external.execute(turn,ConversationActionCall('save','save_document',{
            'path':'source.skill.json','content':json.dumps(current.to_wire())}))
        assert saved.output['status']=='verified'
        actions=ProcedureReferenceActions(engine,authority=authority,gateway_factory=factory,clock=lambda:NOW,executor=executor)
        route=PostgresConversationActions(engine,external_tools=(external,actions))
        ref=route.execute_step(turn,ConversationActionCall('designate','designate_reusable_procedure',{
            'document_path':'source.skill.json','use_when':'uppercase task text','retention_until':UNTIL}),step_index=1).output['reference']
        later=replace(turn,conversation_id='later',message_id='package-two',text='Use skill on a new input')
        journal.admit(later)
        resolved=route.execute_step(later,ConversationActionCall('read','resolve_reusable_procedure',{
            'document_id':ref['document_id'],'resource_path':'references/note.md'}),step_index=1)
        assert resolved.output['references'][0]['untrusted_content']==current.resource('references/note.md').decode()
        run=ConversationActionCall('run','run_reusable_procedure',{'document_id':ref['document_id'],
            'input_json':json.dumps({'files':{'input.txt':'a new task'},'steps':[{'script':'scripts/helper.py','arguments':[]}],'outputs':['output.txt']})})
        result=route.execute_step(later,run,step_index=2)
        assert result.output['status']=='execution_completed',result.output
        output=json.loads(result.output['report']['result']['stdout'])
        assert output['outputs']=={'output.txt':'A NEW TASK'}
        assert result.output['semantic_success_verified'] is False
        assert route.execute_step(later,run,step_index=2)==result and executor.count==1
        with pytest.raises(ConversationConflict):
            route.execute_step(later,replace(run,arguments={**run.arguments,'input_json':'{}'}),step_index=2)
        assert executor.count==1
