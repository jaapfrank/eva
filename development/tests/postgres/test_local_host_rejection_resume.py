"""No-effect evidence, not a permissive exception for uncertain external work."""
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

from eva.adapters.postgres import conversation_journal as journal_module
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.programming_actions import ProgrammingActions
from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.adapters.postgres.work_corrections import reconcile_local_host_rejection
from eva.application.background_execution import BackgroundExecutionScope
from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationModelUsage
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.owned_package_host import _owner_binding
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_delegated_digital_work import delegated_engine
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_runtime_resource_usage import NOW


def test_only_complete_local_and_host_no_dispatch_evidence_can_resume(delegated_engine, monkeypatch):
    clock=[NOW]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return clock[0]
    monkeypatch.setattr(journal_module,'datetime',Clock)
    for case in ('complete','unclosed_model','missing_model','missing_local','external_admission',
                 'colliding_slot','wrong_host_origin','existing_handoff','cleanup_unknown','cleanup_non_boolean','wrong_source','stopped','other_error'):
        clock[0]=NOW;label='host-rejection-'+case;scope,_,binding=_seed_authority(delegated_engine,label);owner=_id(label,'principal')
        store=PostgresDelegatedWorkStore(delegated_engine,scope)
        source='bootstrap/delegated_digital_runner.py';root=Path(__file__).resolve().parents[3]/'src/eva'
        profile=DelegatedDigitalProfile('model:controlled','a'*64,'b'*64,
            ('write_programming_file','get_reviewed_component_application'),
            ((source,hashlib.sha256((root/source).read_bytes()).hexdigest()),))
        service=DelegatedWorkOwnerService(store,owner_principal_id=owner,principal_binding_id=binding,
            clock=lambda:clock[0],digital_profile=profile)
        turn=ConversationTurn.private('tenant:'+label,'installation:'+label,str(owner),'dashboard:'+str(binding),
            'source:'+case,'Prepare a small local package',NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
        journal=PostgresConversationJournal(delegated_engine,scope);journal.admit(turn)
        admitted=service.execute('digital',turn,task=turn.text,request_key='first');spec=store.load(admitted['work']['work_id'],**service._owner).spec
        clock[0]+=timedelta(seconds=1);call=store.start_call(store.claim_next(worker_id='fixture',release=profile.release,now=clock[0]),now=clock[0])
        accounting=BackgroundModelJournal(journal,turn,work_id=spec.work_id);request=accounting.begin_model_request(turn)
        clock[0]+=timedelta(seconds=1)
        if case!='missing_model':accounting.complete_model_request(turn,request,
            ConversationModelUsage.measured(input_tokens=10,output_tokens=10,tool_calls=1),local_stream_closed=None if case=='unclosed_model' else True)
        class Client:
            def request(self,*a,**k):return {'isError':False,'content':[{'type':'text','text':'local fixture'}]}
            def close(self,key):return {'closed':False if case=='cleanup_unknown' else 1 if case=='cleanup_non_boolean' else True}
        client=Client();clock[0]+=timedelta(seconds=1)
        if case!='missing_local':ProgrammingActions(delegated_engine,spec,turn,service.validate_authority,lambda t:None,
            lambda:clock[0],client=client).execute(turn,ConversationActionCall('write','write_programming_file',
                {'path':'/workspace/fixture.py','content':'pass','mode':'rewrite'}),step_index=0)
        source_scope=canonical_sha256({k:getattr(turn,k) for k in ('tenant_id','installation_id','principal_id')})
        evaluator=NS(reviewed_releases=(),owned_package_scopes=(source_scope,),
            application=lambda *a,**k:{'contract':'EvaOwnedPackageCandidate/v1','state':'package_contract','installed':False})
        monkeypatch.setattr('eva.adapters.postgres.component_improvement_actions.configured_component_evaluator',lambda:evaluator)
        ComponentImprovementActions(delegated_engine,spec,turn,service.validate_authority,lambda t:None,
            lambda:clock[0]).execute(turn,ConversationActionCall('contract','get_reviewed_component_application',{'review_id':'0'*64}),step_index=0 if case=='colliding_slot' else 1)
        if case=='external_admission':
            timeline=PostgresOperationalTimeline(delegated_engine,scope)
            timeline.create_thread(thread_id='external:'+label,checkpoint_id='external:'+label+':1',phase='tool_admitted',status='active',
                event_type='TOOL_REQUEST_ADMITTED',occurred_at=clock[0],data={'execution_namespace':BackgroundExecutionScope(turn,spec.execution_id).namespace})
        clock[0]+=timedelta(seconds=1)
        work=store.record_failure(call,usage=DelegatedDigitalRunner._usage(accounting.model_usage(turn),3),error_code='internal_error',
            error_fingerprint='e'*64 if case=='other_error' else hashlib.sha256(b'HTTPError:HTTP Error 400: Bad Request').hexdigest(),ambiguous=True,now=clock[0])
        identity={'execution_id':spec.execution_id,'source':spec.source.model_dump(mode='json'),'source_ingress_sha256':spec.source_ingress_sha256}
        proof={'contract':'EvaOwnedPackageNoDispatch/v1','origin':{'task_id':spec.work_id,'execution_id':spec.execution_id,
            'source_ingress_sha256':spec.source_ingress_sha256,'spec_sha256':spec.sha256,'workspace_key':canonical_sha256(identity),
            'owner_binding_sha256':_owner_binding(spec,scope),'scope_sha256':source_scope},'archive_sha256':'1'*64,'package_sha256':'2'*64,
            'handoff_exists':case=='existing_handoff','application_requested':False,'installed':False}
        if case=='wrong_host_origin':proof['origin']['spec_sha256']='f'*64
        read=lambda **kwargs:proof
        examined=replace(work,stop_requested_at=clock[0]) if case=='stopped' else work
        source_turn=replace(turn,text='changed') if case=='wrong_source' else turn
        receipt=reconcile_local_host_rejection(delegated_engine,examined,source_turn,now=clock[0],
            close_session=client.close,read_host_proof=read,queue_scope=scope)
        assert store.load(spec.work_id,**service._owner)==work
        if case!='complete':assert receipt is None,case;continue
        assert receipt['external_effects_reconciled'] is False
        evaluator.owned_package_no_dispatch=read
        monkeypatch.setattr('eva.adapters.connectors.component_evaluation.configured_component_evaluator',lambda:evaluator)
        monkeypatch.setattr('eva.adapters.connectors.programming_workspace.configured_programming_workspace',lambda:client)
        clock[0]+=timedelta(seconds=profile.timeout_seconds+1)
        scheduler=ScheduledOwnerWork(service);planned=scheduler.schedule(turn,request_key='resume',resume=True,
            request={'work_id':spec.work_id,'run_at':(clock[0]+timedelta(seconds=1)).isoformat().replace('+00:00','Z')})
        clock[0]+=timedelta(seconds=2);assert scheduler.run_once()=='dispatched'
        scheduled=next(x for x in scheduler.list()['schedules'] if x['schedule_id']==planned['schedule_id'])
        child=store.load(scheduled['work_id'],**service._owner)
        assert child.spec.execution_id==spec.execution_id and child.spec.source==spec.source and child.spec.grant==spec.grant
        assert child.spec.parent_task_id==spec.work_id and store.load(spec.work_id,**service._owner)==work
