"""Typed native maintenance authority on existing owner, sensory and work stores."""
from __future__ import annotations
from datetime import UTC, datetime
import re
import sqlalchemy as sa
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.operational_timeline import operational_checkpoints, operational_events

_ROLES = {None, "original_behavior_oracle", "independent_review"}

def _sha(value):
    if not isinstance(value,str) or re.fullmatch(r"[0-9a-f]{64}",value) is None:
        raise ValueError("exact maintenance digest required")
    return value

def _maintenance_policy_value(policy):
    value = policy.get("component_maintenance")
    if (not isinstance(value,dict) or set(value)!={"enabled","authorization_source","packages","target_version"}
            or value['enabled'] is not True or value['packages']!=['psycopg','psycopg-binary']
            or value['target_version']!='3.3.6' or not isinstance(value['authorization_source'],str) or not value['authorization_source'].strip()):
        raise ValueError("current explicit component maintenance mandate required")
    return value


def maintenance_policy(authority):
    policy, digest = authority.policy()
    return _maintenance_policy_value(policy), digest


def _maintenance_attention_observations(authority, connection, scope, principal_id, now):
    """Read one real software source under the CURRENT private owner mandate.

    Called inside the existing review transaction. No connector/model call, new
    transaction, live-version claim, adoption, or household observation is made.
    """
    owner = authority.owner
    if scope != owner._scope or principal_id != str(owner._owner):
        raise ValueError("maintenance context requires its exact private owner")
    owner._authorize(connection, now)
    policy = authority._read()
    if not policy.get("component_maintenance", {}).get("enabled"):
        return ()  # Removing the optional source must not disable ordinary private life.
    maintenance = _maintenance_policy_value(policy)
    mandate = canonical_sha256({k: v for k, v in policy.items() if k != "enabled"})
    c, e = operational_checkpoints.c, operational_events.c
    phase = connection.execute(sa.select(c.phase).where(
        c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
        c.thread_id == authority.thread)).scalar_one()
    if (policy["enabled"] is not True or phase == "initiative_mandate_revoked"
            or authority._admitted_digest(connection) != mandate):
        raise ValueError("maintenance context mandate changed or stopped")
    row = connection.execute(sa.select(e.payload, e.payload_sha256, e.occurred_at).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.event_type == "SENSORY_EVENT_OBSERVED", e.sequence == 1,
        e.thread_id.startswith("sensory:maintenance-"),
        e.payload["data"]["source"].astext == "component_maintenance",
        e.payload["data"]["event_type"].astext == "component_update_available",
        e.payload["data"]["payload"]["component_set"].astext == "psycopg",
        e.occurred_at <= now).order_by(e.occurred_at.desc(), e.thread_id).limit(1)).first()
    if row is None:
        return ()
    if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
        raise ValueError("maintenance sensory source hash differs")
    data = row.payload["data"]; payload = data["payload"]
    observation_sha = _sha(payload["source_sha256"])
    if data["source_event_id"] != "maintenance-" + observation_sha:
        raise ValueError("maintenance sensory source identity differs")
    original = connection.execute(sa.select(e.payload, e.payload_sha256).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == "component-maintenance-source:" + observation_sha,
        e.event_type == "COMPONENT_MAINTENANCE_OBSERVED", e.sequence == 1)).one()
    if (canonical_sha256(original.payload) != bytes(original.payload_sha256).hex()
            or original.payload["data"] != {k: v for k, v in payload.items()
                if k not in {"component_set", "summary"}}):
        raise ValueError("maintenance retained observation differs")
    releases = payload["official_releases"]
    versions = payload["versions"]
    if (set(releases) != set(maintenance["packages"]) or not isinstance(versions, dict)
            or not versions or set(versions) - {"core", "delegated-work-worker"}):
        raise ValueError("maintenance observation package boundary differs")
    for digest in releases.values():
        _sha(digest)
    for packages in versions.values():
        if (not isinstance(packages, dict) or set(packages) != set(maintenance["packages"])
                or any(not isinstance(v, str) or re.fullmatch(r"[0-9A-Za-z.+_-]{1,40}", v) is None
                       for v in packages.values())):
            raise ValueError("maintenance installed version metadata differs")
    return ({"source": "component_maintenance", "source_event_id": data["source_event_id"],
        "source_sha256": bytes(row.payload_sha256).hex(), "observed_at": row.occurred_at.isoformat(),
        "event_type": "component_update_available", "component_set": "psycopg",
        "observation_sha256": observation_sha, "inventory_sha256": _sha(payload["inventory_sha256"]),
        "official_releases": releases, "installed_versions": versions,
        "target_version": maintenance["target_version"],
        "maintenance_configuration_sha256": canonical_sha256(maintenance),
        "classification": "retained_software_metadata_not_current_proof_or_action_permission",
        "is_current_proof": False, "is_execution_permission": False,
        "next_step": "Weigh relevance; use ordinary native work and manage_own_component_update for separately reviewed maintenance"},)


def _work_metadata(authority,service,store,task_id,*,require_running=False):
    from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
    from eva.adapters.postgres.delegated_work import DelegatedWorkCall
    from eva.adapters.postgres.initiative_execution import execution_turn
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    from eva.contracts.timestamps import parse_utc_timestamp, format_required_utc_timestamp
    loaded=store.load(task_id,**service._owner);spec=loaded.spec
    if (type(spec) is not DelegatedInitiativeDigitalWorkSpec or loaded.retention_until<=service.now()
            or loaded.stop_requested_at is not None or loaded.state=='stopped'):
        raise ValueError('current retained native maintenance work required')
    events=authority.timeline.events(spec.origin.thread_id)
    adopted=next((e for e in events if e.event_type=='PERSONAL_INTENT_ADOPTED'),None)
    if adopted is None:raise ValueError('actual native adoption required')
    data=adopted.payload['data'];role=data.get('maintenance_role')
    if role not in _ROLES:raise ValueError('unknown maintenance role')
    claim=PostgresPersonalInterestStore._claim_from_events(authority.owner._tenant_id,
        authority.owner._installation_id,events,spec.origin.claim_revision)
    turn=execution_turn(claim)
    if canonical_sha256(PostgresConversationJournal._ingress_data(turn))!=spec.source_ingress_sha256:
        raise ValueError('maintenance native source differs')
    PostgresConversationJournal(authority.owner._engine,authority.owner._scope).ensure_running(turn)
    if require_running:
        if loaded.state!='call_started' or not loaded.call_id or parse_utc_timestamp(spec.deadline)<=service.now():
            raise ValueError('current running maintenance call required')
        store.authorize_tool_dispatch(DelegatedWorkCall(spec.work_id,loaded.storage_episode_id,loaded.call_id,spec))
    identity={'execution_id':spec.execution_id,'source':spec.source.model_dump(mode='json'),'source_ingress_sha256':spec.source_ingress_sha256}
    usage=None if loaded.usage is None else loaded.usage.to_payload()
    measured=usage is not None and all(type(usage.get(k)) is int and usage[k]>=0 for k in
        ('completed_model_responses','tool_calls','input_tokens','output_tokens','total_tokens')) and usage['completed_model_responses']>0
    return {'task_id':spec.work_id,'intent_id':data['intent_id'],'origin_thread':spec.origin.thread_id,
        'role':role,'maintenance_origin':data.get('maintenance_origin'),
        'target_id':data.get('maintenance_target_id'),'candidate_sha256':data.get('maintenance_candidate_sha256'),
        'comparison_sha256':data.get('maintenance_comparison_sha256'),'adoption_sha256':canonical_sha256(adopted.payload),
        'spec_sha256':spec.sha256,'source_ingress_sha256':spec.source_ingress_sha256,
        'worker_program_sha256':spec.worker_program_sha256,'state':loaded.state,
        'workspace_key':canonical_sha256(identity),'session_key':canonical_sha256({**identity,'work_id':spec.work_id}),
        'usage':usage,'usage_measured':measured,'work_succeeded':loaded.state in {'succeeded','verified'} and loaded.result is not None,
        'result_sha256':None if loaded.result is None else canonical_sha256(loaded.result.to_payload()),
        'retention_until':format_required_utc_timestamp(loaded.retention_until)}

def origin_from_current_work(authority,service,store,task_id,observation_sha256):
    policy,mandate=maintenance_policy(authority);_sha(observation_sha256)
    meta=_work_metadata(authority,service,store,task_id,require_running=True)
    if meta['role'] is not None:
        origin=meta['maintenance_origin']; current_maintenance_origin(authority,origin,service=service,store=store)
        return origin,meta
    source=authority.timeline.events('component-maintenance-source:'+observation_sha256)
    if not source or source[0].event_type!='COMPONENT_MAINTENANCE_OBSERVED' or source[0].payload['data']['source_sha256']!=observation_sha256:
        raise ValueError('retained real installed software observation required')
    scope=canonical_sha256({'tenant_id':authority.owner._tenant_id,'installation_id':authority.owner._installation_id,'principal_id':str(authority.owner._owner)})
    origin={'contract':'EvaComponentMaintenanceOrigin/v1','scope_sha256':scope,'mandate_sha256':mandate,
        'maintenance_policy_sha256':canonical_sha256(policy),'original_work_id':meta['task_id'],
        'original_intent_id':meta['intent_id'],'original_spec_sha256':meta['spec_sha256'],
        'source_ingress_sha256':meta['source_ingress_sha256'],'adoption_sha256':meta['adoption_sha256'],
        'observation_sha256':observation_sha256,'observation_event_sha256':canonical_sha256(source[0].payload)}
    return origin,meta

def _native_turn_locked(authority,connection,thread):
    from eva.adapters.postgres.operational_timeline import _event_from_row
    from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
    from eva.adapters.postgres.initiative_execution import execution_turn
    owner=authority.owner;e=operational_events.c
    row=connection.execute(sa.select(operational_events).where(e.tenant_id==owner._scope.tenant_id,
        e.installation_id==owner._scope.installation_id,e.thread_id==thread,e.event_type=='PERSONAL_INTENT_ADOPTED')).one()
    claim=PostgresPersonalInterestStore._claim_from_events(owner._tenant_id,owner._installation_id,(_event_from_row(row),),0)
    return execution_turn(claim)


def current_maintenance_origin(authority,origin,*,service=None,store=None,_connection=None):
    """Lock mandate -> original intent -> STOP -> native work on one connection."""
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, delegated_work_runs
    expected={'contract','scope_sha256','mandate_sha256','maintenance_policy_sha256','original_work_id',
        'original_intent_id','original_spec_sha256','source_ingress_sha256','adoption_sha256',
        'observation_sha256','observation_event_sha256'}
    if not isinstance(origin,dict) or set(origin)!=expected or origin['contract']!='EvaComponentMaintenanceOrigin/v1':
        raise ValueError('typed maintenance origin required')
    owner=authority.owner;now=service.now() if service is not None else datetime.now(UTC)
    if store is None:
        store=PostgresDelegatedWorkStore(owner._engine,owner._authority,initiative_authority_loader=lambda:authority)
    policy=authority._read();maintenance=policy.get('component_maintenance',{})
    mandate=canonical_sha256({k:v for k,v in policy.items() if k!='enabled'})
    scope=canonical_sha256({'tenant_id':owner._tenant_id,'installation_id':owner._installation_id,'principal_id':str(owner._owner)})
    if (policy['enabled'] is not True or maintenance.get('enabled') is not True or
            origin['scope_sha256']!=scope or origin['mandate_sha256']!=mandate or origin['maintenance_policy_sha256']!=canonical_sha256(maintenance)):
        raise ValueError('maintenance authority changed')
    def locked(connection):
        owner._authorize(connection,now)
        c,e=operational_checkpoints.c,operational_events.c
        mandate_row=connection.execute(sa.select(operational_checkpoints).where(c.tenant_id==owner._scope.tenant_id,
            c.installation_id==owner._scope.installation_id,c.thread_id==authority.thread).with_for_update()).one()
        if mandate_row.phase=='initiative_mandate_revoked' or authority._admitted_digest(connection)!=mandate:
            raise ValueError('maintenance mandate superseded')
        original=connection.execute(owner._query().where(c.thread_id=='intent:'+origin['original_intent_id']).with_for_update(of=operational_checkpoints)).one()
        adoption=original.adoption['data']
        if original.phase in {'personal_stopped','will_abandoned'} or canonical_sha256(original.adoption)!=origin['adoption_sha256']:
            raise ValueError('original maintenance adoption changed or stopped')
        source=connection.execute(sa.select(e.payload,e.payload_sha256).where(e.tenant_id==owner._scope.tenant_id,
            e.installation_id==owner._scope.installation_id,e.thread_id=='component-maintenance-source:'+origin['observation_sha256'],
            e.event_type=='COMPONENT_MAINTENANCE_OBSERVED')).one()
        if canonical_sha256(source.payload)!=bytes(source.payload_sha256).hex() or bytes(source.payload_sha256).hex()!=origin['observation_event_sha256']:
            raise ValueError('original installed observation changed')
        journal=PostgresConversationJournal(owner._engine,owner._scope)
        turn=_native_turn_locked(authority,connection,original.thread_id)
        identity=journal._stop_identity(turn.principal_id,turn.conversation_id,turn.message_id)
        journal._stop_fence(connection,identity)
        if journal._marker_exists(connection,journal._stop_thread_id(identity),'CONVERSATION_STOP_REQUESTED',identity):
            raise ValueError('original maintenance has STOP request')
        # Same established cross-scope native read order; restore owner scope before return.
        PostgresOperationalTimeline(owner._engine,store.scope)._scope(connection)
        try:
            w=delegated_work_runs.c
            row=connection.execute(sa.select(delegated_work_runs).where(w.tenant_id==store._scope_value.tenant_id,
                w.installation_id==store._scope_value.installation_id,w.work_id==origin['original_work_id']).with_for_update()).one()
            if (str(row.owner_principal_id)!=str(owner._owner) or row.retention_until<=now or row.stop_requested_at is not None or row.state=='stopped'
                    or canonical_sha256(row.spec_payload)!=bytes(row.spec_sha256).hex() or bytes(row.spec_sha256).hex()!=origin['original_spec_sha256']
                    or row.spec_payload.get('source_ingress_sha256')!=origin['source_ingress_sha256']):
                raise ValueError('original native maintenance work changed')
        finally:
            authority.timeline._scope(connection)
        return origin
    if _connection is not None:return locked(_connection)
    with owner._engine.begin() as connection:return locked(connection)


def observe_installed_maintenance(authority,observation,*,sensory_store=None,now=None):
    from eva.application.sensory import SensoryEvent
    from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
    from eva.adapters.postgres.operational_timeline import CheckpointConflict
    policy,_=maintenance_policy(authority);now=now or datetime.now(UTC).replace(microsecond=0)
    if set(observation)!={'source_sha256','inventory_sha256','official_releases','versions'}:
        raise ValueError('closed software observation required')
    for key in ('source_sha256','inventory_sha256'):_sha(observation[key])
    if set(observation['official_releases'])!={'psycopg','psycopg-binary'}:
        raise ValueError('unsupported maintenance source')
    for digest in observation['official_releases'].values():_sha(digest)
    owner=authority.owner;identity='component-maintenance-source:'+observation['source_sha256']
    try:
        authority.timeline.create_thread(thread_id=identity,checkpoint_id=identity+':observed',occurred_at=now,
            data=observation,event_type='COMPONENT_MAINTENANCE_OBSERVED',phase='maintenance_observed',status='completed')
    except CheckpointConflict:
        if authority.timeline.events(identity)[0].payload['data']!=observation:raise ValueError('maintenance observation collision')
    sensory_store=sensory_store or PostgresSensoryEventStore(owner._engine,tenant_id=owner._tenant_id,installation_id=owner._installation_id)
    event=SensoryEvent(source='component_maintenance',source_event_id='maintenance-'+observation['source_sha256'],
        event_type='component_update_available',occurred_at=now,payload={**observation,'component_set':'psycopg',
            'summary':'Werkelijk gebruikte Psycopg-driver en officiële update-informatie beschikbaar. Beoordeel relevantie; beheer een gekozen update met manage_own_component_update. Geen verplichte update of opdracht.'})
    return {'state':sensory_store.retain(event),'source_sha256':observation['source_sha256']}

def admit_maintenance_assessment(authority,service,store,*,origin,role,target_id,candidate_sha256=None,comparison_sha256=None):
    from eva.adapters.postgres.initiative_adoption import adopt_private_initiative
    if role not in {'original_behavior_oracle','independent_review'}:raise ValueError('unknown maintenance assessment')
    _sha(target_id)
    current_maintenance_origin(authority,origin,service=service,store=store)
    authority.digital_grant(service.digital_profile)
    if role=='independent_review':_sha(candidate_sha256);_sha(comparison_sha256)
    elif candidate_sha256 is not None or comparison_sha256 is not None:raise ValueError('oracle cannot see candidate binding')
    identity={'origin':canonical_sha256(origin),'role':role,'target_id':target_id,'candidate_sha256':candidate_sha256,'comparison_sha256':comparison_sha256}
    key=canonical_sha256(identity);owner=authority.owner
    task=('Controleer de oorspronkelijke onderhoudsopdracht en onafhankelijke drivercriteria. ' if role=='original_behavior_oracle' else
          'Beoordeel de afzonderlijk gemeten onderhoudskandidaat onafhankelijk op de oorspronkelijke criteria. ')
    task+='Gebruik manage_own_component_update(action=describe) voor uitsluitend jouw hostgebonden invoer. Wijzig geen criteria of eindgevallen. Bewaar '+('oracle.json met target_id, criteria_sha256 en verdict=valid of invalid.' if role=='original_behavior_oracle' else 'review.json met target_id, candidate_sha256, comparison_sha256, decision=accept of reject en behavioral_reason.')+' Rond daarna deze afzonderlijke taak af; geen installatie of externe acties.'
    payload={'principal_id':str(owner._owner),'source_principal_id':str(owner._owner),
        'conversation_id':'initiative-task:'+key,'source_message_id':'initiative-execute:'+key,
        'intent_id':'personal-interest:'+key,'origin':'eva_initiative','execution_mode':'autonomous_task',
        'adoption_source':'automatic_component_maintenance_assessment','unprompted':True,'kind':'personal_interest',
        'title':'Onderhoud afzonderlijk beoordelen ('+key[:12]+')','motivation':'Oorspronkelijke onderhoudsbron en gemeten gebruikersgedrag onafhankelijk beoordelen.',
        'next_exploration':task,'maintenance_origin':origin,'maintenance_role':role,'maintenance_target_id':target_id,
        'maintenance_candidate_sha256':candidate_sha256,'maintenance_comparison_sha256':comparison_sha256,
        'authority':'current_host_component_maintenance_mandate_required'}
    with owner._engine.begin() as connection:
        return adopt_private_initiative(connection,owner._scope,payload,now=service.now(),
            revalidate=lambda:current_maintenance_origin(authority,origin,service=service,store=store,_connection=connection))

def _original_outcome(authority,service,store,origin,*,action,proof,review=None):
    """Preserve builder receipt; publish actual maintenance result on SAME intent."""
    import hashlib
    from eva.adapters.postgres.operational_timeline import transition_timeline_thread,PostgresOperationalTimeline
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.delegated_work import delegated_work_runs
    owner=authority.owner;now=service.now();thread='intent:'+origin['original_intent_id']
    event={'record_submitted':'COMPONENT_MAINTENANCE_SUBMITTED','record_waiting':'COMPONENT_MAINTENANCE_WAITING','close_verified':'COMPONENT_MAINTENANCE_INSTALLED'}[action]
    with owner._engine.begin() as connection:
        current_maintenance_origin(authority,origin,service=service,store=store,_connection=connection)
        c,e=operational_checkpoints.c,operational_events.c
        row=connection.execute(owner._query().where(c.thread_id==thread).with_for_update(of=operational_checkpoints)).one()
        if row.phase!='initiative_completed' or row.status!='completed':
            raise ValueError('original builder not completed or maintenance stopped')
        previous=connection.execute(sa.select(e.event_type,e.payload,e.payload_sha256).where(
            e.tenant_id==owner._scope.tenant_id,e.installation_id==owner._scope.installation_id,e.thread_id==thread,
            e.event_type.in_(('COMPONENT_MAINTENANCE_SUBMITTED','COMPONENT_MAINTENANCE_WAITING','COMPONENT_MAINTENANCE_INSTALLED')))
            .order_by(e.sequence.desc()).limit(1)).one_or_none()
        if previous is not None:
            if canonical_sha256(previous.payload)!=bytes(previous.payload_sha256).hex():raise ValueError('maintenance result history changed')
            if previous.event_type==event and previous.payload['data'].get('proof')==proof:
                return {'state':'completed' if action=='close_verified' else 'waiting','installed':action=='close_verified','replayed':True}
            if previous.event_type=='COMPONENT_MAINTENANCE_INSTALLED':raise ValueError('installed maintenance outcome already closed')
        if review is not None:
            # Same original->child->STOP->work lock order as current native guards.
            child=connection.execute(owner._query().where(c.thread_id==review['origin_thread']).with_for_update(of=operational_checkpoints)).one()
            data=child.adoption['data']
            if (data.get('maintenance_origin')!=origin or data.get('maintenance_role')!='independent_review'
                    or data.get('maintenance_target_id')!=proof['target_id'] or data.get('maintenance_candidate_sha256')!=proof['candidate_sha256']
                    or data.get('maintenance_comparison_sha256')!=proof['comparison_sha256']):raise ValueError('review adoption changed')
            journal=PostgresConversationJournal(owner._engine,owner._scope)
            turn=_native_turn_locked(authority,connection,child.thread_id)
            identity=journal._stop_identity(turn.principal_id,turn.conversation_id,turn.message_id)
            journal._stop_fence(connection,identity)
            if journal._marker_exists(connection,journal._stop_thread_id(identity),'CONVERSATION_STOP_REQUESTED',identity):raise ValueError('maintenance review stopped')
            PostgresOperationalTimeline(owner._engine,store.scope)._scope(connection)
            try:
                w=delegated_work_runs.c
                work=connection.execute(sa.select(delegated_work_runs).where(w.tenant_id==store._scope_value.tenant_id,
                    w.installation_id==store._scope_value.installation_id,w.work_id==review['task_id']).with_for_update()).one()
                if (work.stop_requested_at is not None or work.retention_until<=now or work.state not in {'succeeded','verified'}
                        or bytes(work.spec_sha256).hex()!=review['spec_sha256'] or bytes(work.result_sha256).hex()!=review['result_sha256']):
                    raise ValueError('review retained result changed before completion')
            finally:authority.timeline._scope(connection)
            completed=connection.execute(sa.select(e.payload,e.payload_sha256).where(e.tenant_id==owner._scope.tenant_id,
                e.installation_id==owner._scope.installation_id,e.thread_id==thread,e.event_type=='PERSONAL_INITIATIVE_COMPLETED')
                .order_by(e.sequence.desc()).limit(1)).one()
            if canonical_sha256(completed.payload)!=bytes(completed.payload_sha256).hex():raise ValueError('original builder completion changed')
            result=dict(completed.payload['data'])
            content='Mijn Psycopg-driver is bijgewerkt van 3.3.4 naar 3.3.6. De afzonderlijke beoordeling en controle van de werkelijk gebruikte versie zijn geslaagd; de vorige werkende images zijn bewaard voor herstel.'
            result.update(content=content,content_sha256=hashlib.sha256(content.encode()).hexdigest(),filename='initiatief-resultaat.md',
                provider='eva:verified-component-maintenance',maintenance_state='installed',maintenance_proof=proof,
                builder_result_sha256=result['result_sha256'])
            value=transition_timeline_thread(connection,owner._scope,thread_id=thread,expected_revision=row.revision,
                checkpoint_id=thread+':maintenance-result:'+proof['candidate_sha256'][:20],phase='initiative_completed',status='completed',
                event_type='PERSONAL_INITIATIVE_COMPLETED',occurred_at=now,data=result)
            # Each transition reads the current locked revision; no detached evidence lane.
            row=connection.execute(sa.select(operational_checkpoints).where(c.tenant_id==owner._scope.tenant_id,
                c.installation_id==owner._scope.installation_id,c.thread_id==thread).with_for_update()).one()
        transition_timeline_thread(connection,owner._scope,thread_id=thread,expected_revision=row.revision,
            checkpoint_id=thread+':maintenance:'+canonical_sha256({'event':event,'proof':proof})[:20],
            phase='initiative_completed',status='completed',event_type=event,occurred_at=now,
            data={'origin':origin,'proof':proof,'builder_complete':True,'installed':action=='close_verified'})
        return {'state':'completed' if action=='close_verified' else 'waiting','installed':action=='close_verified'}


def _software_inventory(settings):
    """Current selected names and loaded factory source; never credentials/settings."""
    import inspect,hashlib
    from pathlib import Path
    from importlib import metadata
    providers=[];extensions=[]
    for loaded in (() if settings.external_provider is None else settings.external_provider.providers):
        factory=loaded.factory if inspect.isclass(loaded.factory) else type(loaded.factory)
        module=factory.__module__;path=inspect.getsourcefile(factory)
        row={'provider_id':loaded.provider_id,'module':module,'source_sha256':None,'version':None}
        if path is not None:
            source=Path(path)
            if source.is_file() and not source.is_symlink():row['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        providers.append(row)
        if not module.startswith('eva.providers.'):
            extensions.append(row)
    # Existing active procedure binding, including host-mounted authored code.
    # This is registry metadata, not a camera/voice discovery or execution path.
    import os
    registered=os.environ.get('EVA_HOUSEHOLD_PROCEDURE_FILE')
    if registered:
        binding=Path(registered)
        if binding.is_symlink() or not binding.is_file():raise ValueError('active extension binding unavailable')
        raw=binding.read_bytes()
        if len(raw)>65536:raise ValueError('extension binding metadata too large')
        config=__import__('json').loads(raw)
        if config.get('contract') not in {'EvaHouseholdProcedureBinding/v1','EvaHouseholdProcedureBinding/v2'}:
            raise ValueError('unsupported active procedure binding contract')
        name=config.get('source_file')
        if not isinstance(name,str) or Path(name).name!=name or not name.endswith('.py'):raise ValueError('unsafe registered extension source')
        source=binding.parent/name
        if source.is_symlink() or not source.is_file() or source.stat().st_size>262144:raise ValueError('registered extension source unavailable')
        actual=hashlib.sha256(source.read_bytes()).hexdigest()
        if actual!=config.get('source_sha256'):raise ValueError('registered extension source hash changed')
        extensions.append({'registry':'existing_household_procedure_binding','binding_contract':config['contract'],
            'binding_sha256':hashlib.sha256(raw).hexdigest(),'source_file':name,'source_sha256':actual,
            'reference_id':config.get('reference_id'),'review_sha256':config.get('review_sha256'),
            'enabled':config.get('enabled') is True,'version':None,'version_interpretation':'source identity is authoritative; filename is not a release version'})
    return {'contract':'EvaUsedSoftwareInventory/v1',
        'models':{'conversation_selection':settings.conversation_model,'contact_selection':settings.conversation_contact_model,
            'backend_revision':None,'interpretation':'actual configured selections; no inferred backend release'},
        'connectors':providers,'own_extensions':extensions,
        'external_host_extensions':'Includes the existing active host-mounted procedure registry; unrelated unregistered host programs are not claimed',
        'credentials_included':False}


def handle_host_maintenance_request(request,*,authority=None,service=None,store=None):
    from eva.bootstrap.runtime_repair_host import _context
    if not isinstance(request,dict) or not {'action','scope_sha256'}<=request.keys():raise ValueError('closed maintenance request required')
    with _context(authority,service,store) as (authority,service,store,settings):
        maintenance_policy(authority)
        scope=canonical_sha256({'tenant_id':authority.owner._tenant_id,'installation_id':authority.owner._installation_id,'principal_id':str(authority.owner._owner)})
        if request['scope_sha256']!=scope:raise ValueError('maintenance request scope differs')
        action=request['action'];base={'action','scope_sha256'}
        if action=='inventory' and set(request)==base:
            if settings is None:raise ValueError('actual runtime settings required for installed inventory')
            return _software_inventory(settings)
        if action=='observe' and set(request)==base|{'observation'}:
            return observe_installed_maintenance(authority,request['observation'])
        if action=='binding' and set(request)==base|{'task_id','observation_sha256'}:
            origin,meta=origin_from_current_work(authority,service,store,request['task_id'],request['observation_sha256'])
            return {'origin':origin,'work':meta}
        if 'origin' not in request:raise ValueError('maintenance origin absent')
        origin=request['origin'];current_maintenance_origin(authority,origin,service=service,store=store)
        base|={'origin'}
        if action in {'work_metadata','stage_metadata'} and set(request)==base|{'task_id','require_running'}:
            from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
            if type(request['require_running']) is not bool:raise ValueError('running state must be explicit')
            try:meta=_work_metadata(authority,service,store,request['task_id'],require_running=request['require_running'])
            except DelegatedWorkNotFound:
                if action!='stage_metadata':raise
                return {'state':'awaiting_native_admission','task_id':request['task_id']}
            if meta['task_id']!=origin['original_work_id'] and meta['maintenance_origin']!=origin:raise ValueError('maintenance stage ancestry differs')
            return meta
        if action=='current' and set(request)==base:return origin
        if action=='installed_readback' and set(request)==base:
            meta=_work_metadata(authority,service,store,origin['original_work_id'])
            if not meta['work_succeeded'] or meta['spec_sha256']!=origin['original_spec_sha256']:
                raise ValueError('actual installed store cannot read original completed maintenance work')
            import psycopg
            from psycopg import pq
            return {'contract':'EvaInstalledMaintenanceStoreRead/v1','original_work_id':meta['task_id'],
                'spec_sha256':meta['spec_sha256'],'result_sha256':meta['result_sha256'],
                'loaded_driver':psycopg.__version__,'driver_implementation':pq.__impl__,'libpq':pq.version(),
                'private_content_returned':False,'effects_replayed':False}

        if action=='admit_assessment' and set(request)==base|{'role','target_id','candidate_sha256','comparison_sha256'}:
            if service.digital_profile is None:
                from eva.bootstrap.delegated_digital_composition import build_digital_runner
                service.digital_profile=build_digital_runner(settings,store).profile
            return admit_maintenance_assessment(authority,service,store,**{k:request[k] for k in ('origin','role','target_id','candidate_sha256','comparison_sha256')})
        if action in {'record_submitted','record_waiting','close_verified'} and set(request)==base|{'proof'}:
            proof=request['proof']
            if not isinstance(proof,dict):raise ValueError('typed maintenance proof required')
            meta=None
            if action=='close_verified':
                required={'target_id','candidate_sha256','comparison_sha256','review_sha256','application_receipt_sha256','installed_behavior_sha256','image_recovery_sha256','review_task_id'}
                if set(proof)!=required:raise ValueError('complete installed maintenance proof required')
                for k in required-{'review_task_id'}:_sha(proof[k])
                meta=_work_metadata(authority,service,store,proof['review_task_id'])
                if (meta['maintenance_origin']!=origin or meta['role']!='independent_review' or not meta['work_succeeded'] or not meta['usage_measured']
                        or meta['target_id']!=proof['target_id'] or meta['candidate_sha256']!=proof['candidate_sha256'] or meta['comparison_sha256']!=proof['comparison_sha256']):
                    raise ValueError('exact independent maintenance review missing')
            return _original_outcome(authority,service,store,origin,action=action,proof=proof,review=meta)
        raise ValueError('maintenance action fields differ')


def compose_component_maintenance(runtime,*,attention_store=None):
    """Offer one genuine source on the existing private attention resolver."""
    if attention_store is None or getattr(runtime,'_delegated_work_owner_service',None) is None:
        return None
    required=('_runtime_tenant_id','_runtime_installation_id','_dashboard_owner_principal_id',
        '_dashboard_principal_binding_id','_dashboard_authority_scope')
    if any(getattr(runtime,name,None) is None for name in required):return None
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    try:
        authority=runtime._initiative_authority()
        policy,_=maintenance_policy(authority)
    except (ValueError,InitiativeMandateDenied):
        return None
    previous=attention_store._attention_source_resolver
    from eva.application.sensory import SensoryAttentionSource
    def coverage(event):
        if event.source!='component_maintenance':
            return None if previous is None else previous(event)
        current,_=maintenance_policy(runtime._initiative_authority())
        if event.event_type!='component_update_available' or event.payload.get('component_set')!='psycopg':
            raise ValueError('maintenance event selector differs')
        return SensoryAttentionSource(source='component_maintenance',event_type='component_update_available',
            selector_key='component_set',selector_value='psycopg',configuration_sha256=canonical_sha256(current),
            coverage='Actual installed software and official release evidence; updates require separate native review and managed application')
    attention_store._attention_source_resolver=coverage
    def private_observations(connection, scope, principal_id, now):
        return _maintenance_attention_observations(
            runtime._initiative_authority(), connection, scope, principal_id, now)
    attention_store._private_maintenance_context_reader = private_observations
    return coverage
