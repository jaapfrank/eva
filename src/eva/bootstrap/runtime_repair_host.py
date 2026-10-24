"""Narrow host callbacks for the existing component evaluation/application service.

This is invoked by fixed host code using docker exec, not a conversation/API
tool. It never accepts Python, shell commands, model-selected settings or rights.
"""
from contextlib import contextmanager
from datetime import UTC, datetime
import re

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints, operational_events, transition_timeline_thread,
)
from eva.adapters.postgres.self_repair import current_repair_binding
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import format_required_utc_timestamp


@contextmanager
def _context(authority, service, store):
    if authority is not None or service is not None or store is not None:
        if authority is None or service is None or store is None:
            raise ValueError('complete trusted repair context required')
        yield authority,service,store,None
        return
    from eva.bootstrap.runtime import RuntimeSettings
    from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
    from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    settings=RuntimeSettings.from_environment()
    engine=sa.create_engine(sqlalchemy_postgres_dsn(settings.database_dsn))
    try:
        authority=ProcedureReferenceProviderFactory.native_authority(engine,settings)
        owner=authority.owner
        store=PostgresDelegatedWorkStore(engine,owner._authority,initiative_authority_loader=lambda:authority)
        service=DelegatedWorkOwnerService(store,owner_principal_id=owner._owner,
            principal_binding_id=owner._binding,conversation_scope=owner._scope)
        yield authority,service,store,settings
    finally:
        engine.dispose()


def _work_metadata(authority,service,store,binding,task_id,*,require_running):
    from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
    from eva.adapters.postgres.delegated_work import DelegatedWorkCall
    from eva.adapters.postgres.initiative_execution import execution_turn
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    loaded=store.load(task_id,**service._owner);spec=loaded.spec
    if (type(spec) is not DelegatedInitiativeDigitalWorkSpec
            or loaded.retention_until<=service.now() or loaded.stop_requested_at is not None):
        raise ValueError('current retained native repair work required')
    events=authority.timeline.events(spec.origin.thread_id)
    adopted=next((e for e in events if e.event_type=='PERSONAL_INTENT_ADOPTED'),None)
    if adopted is None or adopted.payload['data'].get('repair_incident_id')!=binding['incident_id']:
        raise ValueError('native work belongs to another repair')
    data=adopted.payload['data'];role=data.get('repair_role')
    if role not in (None,'original_behavior_oracle','independent_review'):
        raise ValueError('unknown native repair work role')
    claim=PostgresPersonalInterestStore._claim_from_events(authority.owner._tenant_id,
        authority.owner._installation_id,events,spec.origin.claim_revision)
    turn=execution_turn(claim)
    if canonical_sha256(PostgresConversationJournal._ingress_data(turn))!=spec.source_ingress_sha256:
        raise ValueError('native repair work source changed')
    PostgresConversationJournal(authority.owner._engine,authority.owner._scope).ensure_running(turn)
    if require_running:
        from eva.contracts.timestamps import parse_utc_timestamp
        if (loaded.state!='call_started' or not loaded.call_id
                or parse_utc_timestamp(spec.deadline)<=service.now()):
            raise ValueError('running repair work is required')
        store.authorize_tool_dispatch(DelegatedWorkCall(spec.work_id,loaded.storage_episode_id,
            loaded.call_id,spec))
    identity={'execution_id':spec.execution_id,'source':spec.source.model_dump(mode='json'),
              'source_ingress_sha256':spec.source_ingress_sha256}
    usage=None if loaded.usage is None else loaded.usage.to_payload()
    measured=(usage is not None and all(type(usage.get(k)) is int and usage[k]>=0 for k in
        ('completed_model_responses','tool_calls','input_tokens','output_tokens','total_tokens'))
        and usage['completed_model_responses']>0)
    return {'contract':'EvaRuntimeRepairWorkMetadata/v1','incident_id':binding['incident_id'],
        'task_id':spec.work_id,'state':loaded.state,'role':role,'target_id':data.get('repair_target_id'),
        'candidate_sha256':data.get('repair_candidate_sha256'),
        'comparison_sha256':data.get('repair_comparison_sha256'),
        'spec_sha256':spec.sha256,'source_ingress_sha256':spec.source_ingress_sha256,
        'worker_program_sha256':spec.worker_program_sha256,
        'workspace_key':canonical_sha256(identity),
        'session_key':canonical_sha256({**identity,'work_id':spec.work_id}),
        'result_sha256':None if loaded.result is None else canonical_sha256(loaded.result.to_payload()),
        'usage':usage,'usage_measured':measured,
        'work_succeeded':loaded.state in {'succeeded','verified'} and loaded.result is not None,
        'retention_until':format_required_utc_timestamp(loaded.retention_until)}


def handle_host_repair_request(request,*,authority=None,service=None,store=None):
    if not isinstance(request,dict) or not {'action','incident_id','scope_sha256'}<=request.keys():
        raise ValueError('invalid host repair request')
    with _context(authority,service,store) as (authority,service,store,settings):
        now=service.now()
        if request['action'] in {'status','close_verified'}:
            saved=_repair_status(authority,request['incident_id'],request['scope_sha256'],now=now)
            if request['action']=='status' and set(request)=={'action','incident_id','scope_sha256'}:
                return saved
            if saved['state']=='repair_repaired':
                if set(request)!={'action','incident_id','scope_sha256','proof'} or request['proof']!=saved.get('proof'):
                    raise ValueError('completed repair replay differs')
                return {'incident_id':request['incident_id'],'state':'repaired',
                        'installed_behavior_verified':True,'replayed':True}
        binding=current_repair_binding(authority,request['incident_id'],now=now)
        if request['scope_sha256']!=binding['scope_sha256']:
            raise ValueError('host repair source scope differs')
        base={'action','incident_id','scope_sha256'}
        action=request['action']
        if action=='binding' and set(request)==base:
            return binding
        if action in {'work_metadata','stage_metadata'} and set(request)==base|{'task_id','require_running'}:
            if type(request['require_running']) is not bool:
                raise ValueError('running-work constraint must be explicit')
            from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
            try:
                return _work_metadata(authority,service,store,binding,request['task_id'],
                    require_running=request['require_running'])
            except DelegatedWorkNotFound:
                if action!='stage_metadata':
                    raise
                return {'state':'awaiting_native_admission','task_id':request['task_id']}
        if action=='admit_assessment' and set(request)==base|{
                'role','target_id','sources','candidate_sha256','comparison_sha256'}:
            if service.digital_profile is None:
                if settings is None:
                    raise ValueError('current native digital profile is required')
                from eva.bootstrap.delegated_digital_composition import build_digital_runner
                # Existing provider/profile composition; construction sends no
                # model request. The existing worker later owns usage/dispatch.
                service.digital_profile=build_digital_runner(settings,store).profile
            from eva.bootstrap.runtime_repair_work import admit_repair_assessment
            return admit_repair_assessment(authority,service,**{key:request[key] for key in
                ('incident_id','role','target_id','sources','candidate_sha256','comparison_sha256')})
        if action=='record_waiting' and set(request)==base|{'outcome','reason_code','handoff_sha256'}:
            if (request['outcome'] not in {'baseline_retained','rejected','blocked','cancelled'}
                    or not isinstance(request['reason_code'],str)
                    or not re.fullmatch(r'[a-z_]{1,80}',request['reason_code'])
                    or not isinstance(request['handoff_sha256'],str)
                    or not re.fullmatch(r'[0-9a-f]{64}',request['handoff_sha256'])):
                raise ValueError('exact bounded repair waiting evidence required')
            owner=authority.owner
            with owner._engine.begin() as connection:
                if current_repair_binding(authority,request['incident_id'],now=service.now(),_connection=connection)!=binding:
                    raise ValueError('repair waiting authority changed')
                c=operational_checkpoints.c
                row=connection.execute(sa.select(operational_checkpoints).where(
                    c.tenant_id==owner._scope.tenant_id,c.installation_id==owner._scope.installation_id,
                    c.thread_id==request['incident_id']).with_for_update()).one()
                transition_timeline_thread(connection,owner._scope,thread_id=row.thread_id,
                    expected_revision=row.revision,
                    checkpoint_id=row.thread_id+':waiting:'+request['handoff_sha256'][:20],
                    phase='repair_investigating',status='waiting_for_proof',
                    event_type='SELF_REPAIR_REMAINS_OPEN',occurred_at=service.now(),
                    data={key:request[key] for key in ('outcome','reason_code','handoff_sha256')})
            return {'state':'waiting_for_repair','installed':False}
        if action=='close_verified' and set(request)==base|{'proof'}:
            return _close_verified(authority,service,store,request['incident_id'],binding,request['proof'],now=now)
        raise ValueError('host repair action fields differ')


def _repair_status(authority,incident_id,scope_sha256,*,now):
    if not isinstance(incident_id,str) or not re.fullmatch(r'self-repair:[0-9a-f]{64}',incident_id):
        raise ValueError('invalid repair incident identity')
    owner=authority.owner
    expected=canonical_sha256({'tenant_id':owner._tenant_id,'installation_id':owner._installation_id,
                              'principal_id':str(owner._owner)})
    if scope_sha256!=expected:
        raise ValueError('repair history source scope differs')
    with owner._engine.begin() as connection:
        owner._authorize(connection,now)
        c,e=operational_checkpoints.c,operational_events.c
        row=connection.execute(sa.select(operational_checkpoints).where(
            c.tenant_id==owner._scope.tenant_id,c.installation_id==owner._scope.installation_id,
            c.thread_id==incident_id)).one_or_none()
        if row is None:
            raise ValueError('repair history unavailable')
        result={'incident_id':incident_id,'state':row.phase,'status':row.status}
        if row.phase=='repair_repaired':
            event=connection.execute(sa.select(e.payload,e.payload_sha256).where(
                e.tenant_id==owner._scope.tenant_id,e.installation_id==owner._scope.installation_id,
                e.thread_id==incident_id,e.sequence==row.revision,
                e.event_type=='SELF_REPAIR_INSTALLED_BEHAVIOR_VERIFIED')).one()
            if canonical_sha256(event.payload)!=bytes(event.payload_sha256).hex():
                raise ValueError('completed repair evidence changed')
            result['proof']=event.payload['data']['proof']
        return result


def _close_verified(authority,service,store,incident_id,binding,proof,*,now):
    # Trusted host attestation sink. The host must verify accepted review bytes,
    # managed installation receipt and installed behavior BEFORE invoking this.
    # Neither a model-supplied hash nor a completed review is that proof.
    fields={'target_id','candidate_sha256','comparison_sha256','independent_review_sha256',
            'application_receipt_sha256','installed_behavior_sha256'}
    if (not isinstance(proof,dict) or set(proof)!=fields|{'review_task_id'} or any(
            not isinstance(proof[key],str) or not re.fullmatch(r'[0-9a-f]{64}',proof[key])
            for key in fields)):
        raise ValueError('exact host-verified application and behavior proof required')
    review=_work_metadata(authority,service,store,binding,proof['review_task_id'],require_running=False)
    if (not review['work_succeeded'] or not review['usage_measured']
            or review['role']!='independent_review' or review['target_id']!=proof['target_id']
            or review['candidate_sha256']!=proof['candidate_sha256']
            or review['comparison_sha256']!=proof['comparison_sha256']):
        raise ValueError('the exact separate repair review is not complete')
    retained=store.load(proof['review_task_id'],**service._owner)
    from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
    from eva.adapters.postgres.initiative_execution import execution_turn
    review_turn=execution_turn(PostgresPersonalInterestStore._claim_from_events(
        authority.owner._tenant_id,authority.owner._installation_id,
        authority.timeline.events(retained.spec.origin.thread_id),retained.spec.origin.claim_revision))
    owner=authority.owner
    with owner._engine.begin() as connection:
        now=service.now()
        current=current_repair_binding(authority,incident_id,now=now,_connection=connection)
        if current!=binding:
            raise ValueError('repair authority changed before completion')
        # Original ancestry is locked first, then this exact review intent,
        # then its STOP fence and retained work. A concurrent STOP either wins
        # before completion or observes a completed transition afterwards.
        c=operational_checkpoints.c
        child=connection.execute(owner._query().where(
            c.thread_id==retained.spec.origin.thread_id).with_for_update(
                of=operational_checkpoints)).one()
        data=child.adoption['data']
        if (data.get('repair_incident_id')!=incident_id
                or data.get('repair_role')!='independent_review'
                or data.get('repair_target_id')!=proof['target_id']
                or data.get('repair_candidate_sha256')!=proof['candidate_sha256']
                or data.get('repair_comparison_sha256')!=proof['comparison_sha256']):
            raise ValueError('repair review adoption changed before completion')
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        from eva.adapters.postgres.delegated_work import delegated_work_runs
        journal=PostgresConversationJournal(owner._engine,owner._scope)
        identity=journal._stop_identity(review_turn.principal_id,review_turn.conversation_id,review_turn.message_id)
        journal._scope(connection)
        journal._stop_fence(connection,identity)
        if journal._marker_exists(connection,journal._stop_thread_id(identity),
                'CONVERSATION_STOP_REQUESTED',identity):
            raise ValueError('repair review stopped before completion')
        # This host transaction intentionally crosses the already authorized
        # conversation and work scopes, exactly like native retained-work reads.
        from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
        PostgresOperationalTimeline(owner._engine,store.scope)._scope(connection)
        store._validate_owner_binding(connection,**service._owner)
        w=delegated_work_runs.c
        connection.execute(sa.select(w.work_id).where(
            w.tenant_id==store._scope_value.tenant_id,
            w.installation_id==store._scope_value.installation_id,
            w.work_id==retained.spec.work_id).with_for_update()).one()
        latest=store._load_locked(connection,retained.spec.work_id)
        if (latest.spec!=retained.spec or latest.retention_until<=now
                or latest.stop_requested_at is not None
                or latest.state not in {'succeeded','verified'} or latest.result is None
                or canonical_sha256(latest.result.to_payload())!=review['result_sha256']):
            raise ValueError('repair review changed before completion')
        owner._authorize(connection,now)
        row=connection.execute(sa.select(operational_checkpoints).where(
            c.tenant_id==owner._scope.tenant_id,c.installation_id==owner._scope.installation_id,
            c.thread_id==incident_id).with_for_update()).one()
        transition_timeline_thread(connection,owner._scope,thread_id=incident_id,
            expected_revision=row.revision,checkpoint_id=incident_id+':installed:'+proof['candidate_sha256'][:20],
            phase='repair_repaired',status='completed',event_type='SELF_REPAIR_INSTALLED_BEHAVIOR_VERIFIED',
            occurred_at=now,data={'proof':proof,'source_binding_sha256':canonical_sha256(binding)})
        return {'incident_id':incident_id,'state':'repaired','installed_behavior_verified':True}
