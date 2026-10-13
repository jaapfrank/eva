"""Ordinary task admission to real isolated gateway/native worker effects."""
import asyncio, hashlib, json, os, subprocess
from pathlib import Path
from datetime import datetime, UTC
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory, DocumentGoalActionSettings
from eva.application.conversation import ConversationTurn,ConversationActionCall
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_operational_timeline import timeline_engine


def test_ordinary_tasks_native_restart_stop_and_no_overwrite(timeline_engine,private_tmp_path):
    repeatable=os.environ.get('EVA2_DOCUMENT_REPEATABLE_PROOF')=='1'
    stack=json.loads(Path(os.environ['EVA2_DOCUMENT_PRODUCT_STACK']).read_text())
    assert stack['names']['network'].startswith('eva-atomic-gateway-')
    def docker(*args):
        return subprocess.check_output(['sudo','-n','docker',*args],text=True)
    info=json.loads(docker('inspect',stack['names']['gateway']))[0]
    assert not info['HostConfig']['PortBindings']
    address=info['NetworkSettings']['Networks'][stack['names']['network']]['IPAddress']
    gateway='http://'+address+':4444'
    tools=[]
    for label in ('happy','stop'):
        rows=stack['instances'][label]['selectedTools']
        execute,observe=rows['workspace_create_bound'],rows['workspace_observe_bound']
        schema=execute['inputSchema'];names=list(schema['properties'])
        tools.append({'eva_name':'create_'+label,'description':'Isolated bound document provider',
            'completion_guidance':'Only verified created receipts','input_schema':schema,'operation_kind':'effect',
            'execute_catalog_name':execute['name'],'execute_tool_name':execute['name'],
            'observe_catalog_name':observe['name'],'observe_tool_name':observe['name'],
            'execute_catalog_sha256':canonical_sha256(mcp_gateway_catalog_binding(execute,contract='EvaMcpGatewayProvider/v3')),
            'observe_catalog_sha256':canonical_sha256(mcp_gateway_catalog_binding(observe,contract='EvaMcpGatewayProvider/v3')),
            'execute_argument_bindings':[{'argument_name':n,'provider_name':n} for n in names],
            'observe_argument_bindings':[{'argument_name':n,'provider_name':n} for n in names],
            'proof_bindings':[{'argument_name':n,'observed_name':n} for n in ('operation_id','request_binding_sha256','path','content')]})
    catalog=json.dumps({'contract':'EvaMcpGatewayProvider/v3','tools':tools})
    specs=parse_mcp_gateway_catalog(catalog)
    def private(name,content):
        path=private_tmp_path/name;path.write_text(content);path.chmod(0o600);return path
    catalogfile=private('catalog',catalog)
    poolfile=private('pool',json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':label,'tool_configuration_sha256':spec.configuration_sha256} for label,spec in zip(('happy',) if repeatable else ('happy','stop'),specs)]}))
    mandate={'contract':'OwnerConnectorMandate/v1','mandate_id':'task-native','tenant_id':'task-native',
        'installation_id':'isolated','owner_principal_id':'owner','issued_at':'2020-01-01T00:00:00Z',
        'authorization_source':'isolated test','enabled':True,'public_https':False,'local_networks':[]}
    mandatefile=private('mandate',json.dumps(mandate))
    authority=McpOwnerAuthority(timeline_engine,str(mandatefile))
    store=PostgresDurableGoalStore(timeline_engine,tenant_id='task-native',installation_id='isolated')
    journal=PostgresConversationJournal(timeline_engine,authority.scope)
    settings=DocumentGoalActionSettings('','',str(mandatefile),'task-native','isolated',str(poolfile))
    now=datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00','Z')
    def call(message,name,args):
        turn=ConversationTurn.private('task-native','isolated','owner','chat',message,'Maak uitnodiging en korte apptekst.',now)
        journal.admit(turn)
        bundle=DocumentGoalActionsProviderFactory.build(settings,timeline_engine)
        route=PostgresConversationActions(timeline_engine,external_tools=bundle.external_tools)
        return route.execute(turn,ConversationActionCall(message,name,args)).output['result']
    task={'title':'Gezinsdag','first_document':'Beste familie, zondag om 12 uur samen lunchen. Neem niets mee.',
        'second_document':'Zondag 12u lunch bij ons? Je hoeft niets mee te nemen.'}
    first=call('one','start_document_project',task)
    if repeatable:
        pool=json.loads(poolfile.read_text());pool.update(contract='EvaDocumentTaskPool/v2',dynamic_tool_configuration_sha256=specs[1].configuration_sha256)
        poolfile.write_text(json.dumps(pool))
    second=call('two','start_document_project',{**task,'title':'Tweede uitnodiging','first_document':'Beste buren, zaterdag koffie.',
        'second_document':'Zaterdag koffie bij ons?'})
    assert first['goal_id']!=second['goal_id']
    more=[call('extra-'+str(i),'start_document_project',{**task,'title':'Extra '+str(i)}) for i in range(2)] if repeatable else []
    assert len({x['goal_id'] for x in [first,second,*more]})==2+len(more)
    def material(instance):
        code="import json,hashlib;from pathlib import Path;print(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in ['/workspace','/journal'] for p in Path(root).rglob('*') if p.is_file()}))"
        return json.loads(docker('exec',instance['provider'],'python3','-c',code))
    assert all(material(stack['instances'][label])=={} for label in ('happy','stop'))
    env={'EVA_MCP_GATEWAY_URL':gateway,'EVA_MCP_GATEWAY_TOKEN_FILE':stack['tokenFile'],
        'EVA_MCP_GATEWAY_CATALOG_FILE':str(catalogfile),'EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP':'true',
        'EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST':hashlib.sha256(b'owner').hexdigest(),
        'EVA_MCP_AUTONOMY_MANDATE_FILE':str(mandatefile),'EVA_DOCUMENT_TASK_POOL_FILE':str(poolfile)}
    child = r"""
import asyncio,json,os,sys
import sqlalchemy as sa
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
import eva.bootstrap.document_goal_worker as boot
engine=sa.create_engine(sys.argv[1]);store=PostgresDurableGoalStore(engine,tenant_id='task-native',installation_id='isolated')
selected={}
def capture(**kwargs):selected.update(kwargs);return 0
boot.run_loop=capture;boot.main()
goal=store.state(sys.argv[2],'owner').spec
worker=selected['worker_factory'](engine,store,goal,'task-native','isolated')
record=store.record_effect
def checkpoint(*args):
    value=record(*args)
    if sys.argv[3]=='crash':os._exit(71)
    return value
store.record_effect=checkpoint
state=asyncio.run(worker.on_wake(goal))
print(state.status)
"""
    def run(goal,mode):
        source=Path(__file__).resolve().parents[3]/'src';socket=timeline_engine.url.query['host']
        args=['run','--rm','--read-only','--network',stack['names']['network'],'--cap-drop','ALL',
            '--security-opt','no-new-privileges','--user',str(os.getuid())+':'+str(os.getgid()),'--tmpfs','/tmp:rw,nosuid,nodev,size=32m',
            '--mount',f'type=bind,src={source},dst=/opt/eva/src,readonly',
            '--mount',f'type=bind,src={private_tmp_path},dst={private_tmp_path}',
            '--mount',f'type=bind,src={socket},dst={socket}',
            '--mount',f"type=bind,src={stack['tokenFile']},dst={stack['tokenFile']},readonly",
            '-e','PYTHONPATH=/opt/eva/src','-e','PYTHONDONTWRITEBYTECODE=1']
        for key,value in env.items():args+=['-e',key+'='+value]
        args+=['--entrypoint','/usr/local/bin/python',os.environ.get('EVA2_DOCUMENT_CORE_IMAGE','sha256:832983322ad7d71611225749d618e828d9dd4676f8a24711f09e0ac349db1fcd'),
            '-c',child,timeline_engine.url.render_as_string(hide_password=False),goal,mode]
        result=subprocess.run(['sudo','-n','docker',*args],capture_output=True,text=True,timeout=90)
        assert result.returncode==(71 if mode=='crash' else 0),result.stdout+result.stderr
    run(first['goal_id'],'crash')
    before=material(stack['instances']['happy'])
    state=store.state(first['goal_id'],'owner');assert state.completed_steps==1
    run(first['goal_id'],'normal')
    finished=call('status-one','get_document_project',{'goal_id':first['goal_id']})
    assert finished['project_status']=='completed'
    assert finished['first_document']['content']==task['first_document']
    assert finished['final_document']['content']==task['second_document']
    after=material(stack['instances']['happy'])
    firstpath='/workspace/'+Path(finished['first_document']['path']).name
    assert before[firstpath]==after[firstpath]
    run(second['goal_id'],'crash')
    second_before=material(stack['instances']['stop'])
    assert call('stop-two','stop_document_project',{'goal_id':second['goal_id']})['project_status']=='cancelled'
    run(second['goal_id'],'normal')
    assert material(stack['instances']['stop'])==second_before
    assert material(stack['instances']['happy'])==after

    if repeatable:
        docker('restart',stack['instances']['stop']['provider'])
        import time
        for _ in range(45):
            info=json.loads(docker('inspect',stack['instances']['stop']['provider']))[0]
            if info['State'].get('Health',{}).get('Status')=='healthy':break
            time.sleep(1)
        else:raise AssertionError('isolated provider did not recover')
        for index,item in enumerate(more):
            run(item['goal_id'],'normal')
            result=call('extra-status-'+str(index),'get_document_project',{'goal_id':item['goal_id']})
            assert result['project_status']=='completed'
            assert result['first_document']['content']==task['first_document']
            assert result['final_document']['content']==task['second_document']
        assert material(stack['instances']['happy'])==after
        listed=call('all-projects','list_document_projects',{})
        assert {p['goal_id'] for p in listed['projects']}=={p['goal_id'] for p in [first,second,*more]}
