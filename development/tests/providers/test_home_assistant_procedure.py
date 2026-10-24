from datetime import UTC, datetime, timedelta
from copy import deepcopy
from types import SimpleNamespace
from dataclasses import replace
import hashlib,json
import pytest
from eva.providers.home_assistant_procedure import HomeAssistantProcedure
from tests.connectors.test_camera_observer import binding, inventory, event
from eva.adapters.connectors.camera_observer import discover_cameras
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


def configured(tmp_path,monkeypatch):
    import eva.providers.home_assistant_procedure as module
    now=datetime.now(UTC).replace(microsecond=0); household=binding();registry=discover_cameras(*inventory())
    camera={'contract':'EvaCameraObservation/v1','status':'observed','camera_entity_id':'camera.unrelated_small',
        'device_id':'arbitrary-device','discovery_revision':registry.revision,'alternate_camera_count':1,
        'captured_at':None,'retrieved_at':now.isoformat(),'image_sha256':'a'*64,
        'interpretation_not_verified_fact':True,'identity_is_not_authentication':True}
    raw=replace(event(),occurred_at=now,payload={**event().payload,'camera_observation':camera})
    wrapped=household.observation_event(raw)
    source=b'def propose(event,binding,*,now): return {}\n';(tmp_path/'extension.py').write_bytes(source)
    sha=hashlib.sha256(source).hexdigest()
    config={'contract':'EvaHouseholdProcedureBinding/v1','enabled':True,'household_binding':household.to_payload(),
        'source_file':'extension.py','source_sha256':sha,'reference_id':'procedure-reference:test',
        'source_principal_id':'owner','review_sha256':'b'*64}
    path=tmp_path/'binding.json';path.write_text(json.dumps(config))
    doc=SimpleNamespace(tenant_id='tenant',installation_id='install',principal_id='owner',conversation_id='chat',source_ref='message',project_id=None,content=json.dumps({'contract':'ProcedureReference/v1','content_sha256':sha,'retention_until':'2099-01-01T00:00:00Z'}))
    monkeypatch.setattr(module,'PostgresMemoryOutbox',lambda *a,**kw:SimpleNamespace(resolve_source=lambda *a,**kw:doc))
    from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
    from eva.application.procedure_references import ProcedureReferences
    monkeypatch.setattr(ProcedureReferenceProviderFactory,'load_settings',lambda:None)
    monkeypatch.setattr(ProcedureReferenceProviderFactory,'build',lambda *a:SimpleNamespace(external_tools=[
        SimpleNamespace(source_loader=lambda source:object(),_host=lambda turn:object())]))
    monkeypatch.setattr(ProcedureReferences,'resolve_candidates',lambda *a,**kw:(SimpleNamespace(untrusted_content=source.decode()),))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_bytes(self):yield json.dumps({'profile_revision':'c'*64}).encode()
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def stream(self,*a,**kw):return Response()
    monkeypatch.setattr(module.httpx,'Client',Client)
    output={'action':'propose_greeting','version':'fixture','text':'Welcome','delivery_key':'d'*64,
        'camera_entity_id':camera['camera_entity_id'],'source_event_id':raw.source_event_id,
        'image_sha256':camera['image_sha256'],'expires_at_epoch':float(now.timestamp()+30),
        'capture_time_verified':False,'identity_is_not_authentication':True,
        'requires_existing_household_assessment_stop_and_durable_delivery':True}
    inputs=[]
    def execute(source,data):
        inputs.append(data)
        return {'result':{'exit_code':0,'timed_out':False,'cancelled':False,'stdout_truncated':False,
                          'stderr_truncated':False,'stdout':json.dumps(output)}}
    settings=SimpleNamespace(endpoint_url='http://localhost',credential='host-only',face_endpoint='http://localhost',
                             face_credential='face-secret',face_revision='test')
    (tmp_path/'executor-token').write_text('private-test-token')
    proposal=HomeAssistantProcedure(path,settings=settings,engine=object(),household=household,
        authorize=lambda:None,executor=SimpleNamespace(execute=execute,url='http://executor.invalid',image_id='sha256:'+'f'*64,timeout_seconds=30,output_bytes=4096,token_file=str(tmp_path/'executor-token')))
    proposal.discovery=SimpleNamespace(discover=lambda:registry)
    return proposal,wrapped,now,inputs,output,path


def test_bound_proposal_uses_existing_executor_and_never_passes_credentials(tmp_path,monkeypatch):
    p,event,now,inputs,output,path=configured(tmp_path,monkeypatch)
    context=p(event,now=now)
    assert type(context['expires_at_epoch']) is int
    assert context['is_execution_permission'] is False
    assert len(inputs)==1 and 'host-only' not in str(inputs) and 'face-secret' not in str(inputs)
    p.validate_current(context);p.validate_expression(event,context['binding_sha256'])
    config=json.loads(path.read_text());config['enabled']=False;path.write_text(json.dumps(config))
    with pytest.raises(ValueError):p.validate_expression(event,context['binding_sha256'])


@pytest.mark.parametrize('change',['wrong_event','expired','fractional','authority','extra'])
def test_invalid_sandbox_output_never_becomes_a_proposal(tmp_path,monkeypatch,change):
    p,event,now,inputs,out,path=configured(tmp_path,monkeypatch)
    if change=='wrong_event':out['source_event_id']='different'
    if change=='expired':out['expires_at_epoch']=now.timestamp()-1
    if change=='fractional':out['expires_at_epoch']+=0.25
    if change=='authority':out['identity_is_not_authentication']=False
    if change=='extra':out['execute']='speak now'
    with pytest.raises(ValueError):p(event,now=now)


def test_changed_source_stops_before_execution(tmp_path,monkeypatch):
    p,event,now,inputs,out,path=configured(tmp_path,monkeypatch)
    (tmp_path/'extension.py').write_text('changed')
    with pytest.raises(ValueError):p(event,now=now)
    assert not inputs


def test_transport_config_change_invalidates_prepared_guard(tmp_path,monkeypatch):
    p,event,now,inputs,out,path=configured(tmp_path,monkeypatch)
    context=p(event,now=now)
    p.settings.credential='changed-account'
    with pytest.raises(ValueError):p.validate_expression(event,context['binding_sha256'])


def camera_child(wrapped, now, *, age=2, identifier='fresh', faces=True):
    child=deepcopy(wrapped.payload['observation'])
    child['source_event_id']='home-assistant:'+identifier
    child['occurred_at']=(now-timedelta(seconds=age)).isoformat()
    camera=child['payload']['camera_observation']
    camera['recognition']={'contract':'EvaLocalFaceObservation/v1','image_sha256':camera['image_sha256'],
        'analyzed_at':now.isoformat(),'backend':'arcface','backend_revision':'test',
        'profile_revision':'c'*64,'evidence_kind':'face_similarity_candidate_not_authentication',
        'faces':[{'region':[0,0,50,50],'status':'unknown'}] if faces else []}
    return child


def sensor_child(now, *, age=100, identifier='temperature', description='Unrelated sensor'):
    return {'source':'home_assistant','source_event_id':'home-assistant:'+identifier,
        'event_type':'state_changed','occurred_at':(now-timedelta(seconds=age)).isoformat(),
        'payload':{'entity_id':'sensor.'+identifier,'old_state':'20','new_state':'21',
                   'friendly_name':description}}


def store_shaped_batch(proposal, members):
    payload={'observations':deepcopy(members),'selection_limit':16,
             'historical_observations_not_live_proof':True}
    raw=SensoryEvent('home_assistant','home-assistant:initiative-batch:'+canonical_sha256(payload),
        'household_observation_batch',min(datetime.fromisoformat(x['occurred_at']) for x in members),payload)
    return proposal.household.observation_event(raw)


def producer_selected_batch(proposal, members, *, truncated_at=None):
    from eva.adapters.postgres.sensory_events import _select_household_observation_members
    # The producer's actual selection, not a test-owned deduplication algorithm.
    sources=[SensoryEvent(x['source'],x['source_event_id'],x['event_type'],
             datetime.fromisoformat(x['occurred_at']),deepcopy(x['payload'])) for x in members]
    sources.sort(key=lambda x:(-x.occurred_at.timestamp(),'sensory:'+x.source_event_id))
    selected,public,_=_select_household_observation_members(
        sources,proposal.household,truncated_at=truncated_at)
    if not selected:return None
    if len(sources)==1:return proposal.household.observation_event(selected[0])
    return store_shaped_batch(proposal,public)


def output_for_child(output, child):
    camera=child['payload']['camera_observation']
    output.update(source_event_id=child['source_event_id'],camera_entity_id=camera['camera_entity_id'],
        image_sha256=camera['image_sha256'],
        expires_at_epoch=datetime.fromisoformat(child['occurred_at']).timestamp()+30)


def frozen_clock(monkeypatch, now):
    import eva.providers.home_assistant_procedure as module
    clock=[now]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return clock[0]
    monkeypatch.setattr(module,'datetime',Clock)
    return clock


def test_real_batch_shape_uses_fresh_child_not_old_parent_trigger(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    child=camera_child(wrapped,now)
    batch=store_shaped_batch(p,[sensor_child(now),child]);before=deepcopy(batch)
    output_for_child(out,child)
    context=p(batch,now=now)
    assert inputs[0]['event']==child and inputs[0]['binding']['source_event_id']==child['source_event_id']
    assert inputs[0]['binding']['occurred_at']==child['occurred_at']
    assert context['expires_at_epoch']==int(datetime.fromisoformat(child['occurred_at']).timestamp()+30)
    assert context['parent_observation_sha256']==canonical_sha256(batch.payload['observation'])
    assert context['observation']==child and batch==before
    p.validate_current(context);p.validate_expression(batch,context['binding_sha256'])
    assert len(inputs)==1  # Neither current-source validation executes the procedure again.


@pytest.mark.parametrize('newest',['empty','unavailable','analysis_unavailable','stale_trigger','future'])
def test_newest_camera_supersedes_older_face_even_when_ineligible(tmp_path,monkeypatch,newest):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    old=camera_child(wrapped,now,age=5,identifier='old-face')
    latest=camera_child(wrapped,now,age=-1 if newest=='future' else 1,identifier='newest',faces=False)
    if newest not in {'empty','future'}:latest['payload']['camera_observation']['status']=newest
    batch=store_shaped_batch(p,[old,latest])
    assert p(batch,now=now) is None and not inputs


def test_conflicting_same_second_camera_frames_do_not_resurrect_a_face(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    positive=camera_child(wrapped,now,identifier='positive')
    empty=camera_child(wrapped,now,identifier='empty',faces=False)
    assert p(store_shaped_batch(p,[positive,empty,positive]),now=now) is None
    assert not inputs


@pytest.mark.parametrize('latest_status',['unavailable','empty'])
def test_real_producer_keeps_new_camera_refusal_despite_later_binary_reset(tmp_path,monkeypatch,latest_status):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    positive=camera_child(wrapped,now,age=5,identifier='older-positive')
    newer=camera_child(wrapped,now,age=3,identifier='newer-camera',faces=False)
    if latest_status=='unavailable':
        camera=newer['payload']['camera_observation'];camera['status']='unavailable'
        camera.pop('recognition');camera.pop('image_sha256')
    reset=deepcopy(newer);reset['source_event_id']='home-assistant:reset'
    reset['occurred_at']=(now-timedelta(seconds=1)).isoformat()
    reset['payload']={k:v for k,v in reset['payload'].items() if k!='camera_observation'}
    reset['payload']['new_state']='off'
    batch=producer_selected_batch(p,[positive,newer,reset])
    members=batch.payload['observation']['payload']['observations']
    assert {x['source_event_id'] for x in members}=={newer['source_event_id'],reset['source_event_id']}
    assert p(batch,now=now) is None and not inputs


def test_real_producer_preserves_current_camera_beside_later_reset(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    camera=camera_child(wrapped,now,age=3)
    reset=deepcopy(camera);reset['source_event_id']='home-assistant:reset'
    reset['occurred_at']=(now-timedelta(seconds=1)).isoformat()
    reset['payload']={k:v for k,v in reset['payload'].items() if k!='camera_observation'}
    reset['payload']['new_state']='off'
    output_for_child(out,camera)
    context=p(producer_selected_batch(p,[camera,reset]),now=now)
    assert context['observation']==camera and inputs[0]['event']==camera and len(inputs)==1


@pytest.mark.parametrize('with_reset',[False,True])
def test_real_producer_refuses_lex_first_positive_in_same_second_conflict(tmp_path,monkeypatch,with_reset):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    positive=camera_child(wrapped,now,age=3,identifier='a-positive')
    empty=camera_child(wrapped,now,age=3,identifier='z-empty',faces=False)
    output_for_child(out,positive)
    members=[positive,empty]
    if with_reset:members.append(sensor_child(now,age=1))
    batch=producer_selected_batch(p,members)
    assert batch is None or p(batch,now=now) is None
    assert not inputs


@pytest.mark.parametrize('boundary',['members','bytes'])
def test_real_producer_resolves_camera_conflict_before_batch_limits(tmp_path,monkeypatch,boundary):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    positive=camera_child(wrapped,now,age=3,identifier='a-positive')
    empty=camera_child(wrapped,now,age=3,identifier='z-empty',faces=False)
    fillers=[sensor_child(now,age=3,identifier='middle'+str(i),
             description='x'*600 if boundary=='bytes' else 'sensor')
             for i in range(7 if boundary=='bytes' else 16)]
    output_for_child(out,positive)
    batch=producer_selected_batch(p,[positive,*fillers,empty])
    public=batch.payload['observation']['payload']['observations']
    assert len(public)<=16 and len(canonical_json_bytes(public))<=6144
    assert not any('camera_observation' in child['payload'] for child in public)
    assert p(batch,now=now) is None and not inputs


@pytest.mark.parametrize('pending_count',[1,32])
def test_real_producer_does_not_assert_unambiguous_camera_at_sql_page_cutoff(tmp_path,monkeypatch,pending_count):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    positive=camera_child(wrapped,now,age=3,identifier='a-positive')
    # In the one-pending case the other31 original rows were routine-filtered.
    # The caller still supplies the cutoff from its full ORIGINAL SQL page.
    fillers=[sensor_child(now,age=1,identifier='newer'+str(i)) for i in range(pending_count-1)]
    output_for_child(out,positive)
    batch=producer_selected_batch(p,[positive,*fillers],
        truncated_at=datetime.fromisoformat(positive['occurred_at']))
    assert batch is None or p(batch,now=now) is None
    assert not inputs


def test_identical_normalized_camera_child_duplicate_is_not_a_conflict(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    camera=camera_child(wrapped,now)
    output_for_child(out,camera)
    context=p(producer_selected_batch(p,[camera,deepcopy(camera)]),now=now)
    assert context['observation']==camera and inputs[0]['event']==camera and len(inputs)==1


def test_newer_unambiguous_camera_supersedes_older_same_second_conflict(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    old=camera_child(wrapped,now,age=5,identifier='a-positive')
    empty=camera_child(wrapped,now,age=5,identifier='z-empty',faces=False)
    fresh=camera_child(wrapped,now,age=2,identifier='fresh-next-event')
    output_for_child(out,fresh)
    context=p(producer_selected_batch(p,[old,empty,fresh]),now=now)
    assert context['observation']==fresh and inputs[0]['event']==fresh and len(inputs)==1


def test_another_current_camera_can_supply_proposal_after_first_camera_empty(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    rows,states=inventory();more_rows,more_states=inventory('another','second-device')
    registry=discover_cameras(rows+more_rows,states+more_states)
    p.discovery=SimpleNamespace(discover=lambda:registry)
    old=camera_child(wrapped,now,age=5,identifier='old-face')
    empty=camera_child(wrapped,now,age=1,identifier='latest-empty',faces=False)
    another=camera_child(wrapped,now,age=3,identifier='another-camera')
    another['payload']['entity_id']='binary_sensor.another'
    another['payload']['camera_observation'].update(device_id='second-device',camera_entity_id='camera.another_small')
    for child in [old,empty,another]:child['payload']['camera_observation']['discovery_revision']=registry.revision
    output_for_child(out,another)
    context=p(producer_selected_batch(p,[old,empty,another]),now=now)
    assert context['observation']==another and inputs[0]['event']==another and len(inputs)==1


@pytest.mark.parametrize('change',['unselected_parent','selected_image','selected_id','reorder'])
def test_stored_parent_or_selected_child_tampering_invalidates_delivery(tmp_path,monkeypatch,change):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    child=camera_child(wrapped,now);members=[sensor_child(now),child]
    output_for_child(out,child);context=p(store_shaped_batch(p,members),now=now)
    changed=deepcopy(members)
    if change=='unselected_parent':changed[0]['payload']['new_state']='22'
    if change=='selected_image':
        camera=changed[1]['payload']['camera_observation'];camera['image_sha256']='e'*64
        camera['recognition']['image_sha256']='e'*64
    if change=='selected_id':changed[1]['source_event_id']='home-assistant:changed-source'
    if change=='reorder':changed.reverse()
    with pytest.raises(ValueError):p.validate_expression(store_shaped_batch(p,changed),context['binding_sha256'])
    assert len(inputs)==1


@pytest.mark.parametrize('change',['parent_digest','child_metadata'])
def test_after_model_context_cannot_change_parent_stamp_or_child(tmp_path,monkeypatch,change):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    child=camera_child(wrapped,now);output_for_child(out,child)
    context=p(store_shaped_batch(p,[child]),now=now)
    if change=='parent_digest':context['parent_observation_sha256']='0'*64
    else:context['observation']['payload']['new_state']='off'
    with pytest.raises(ValueError):p.validate_current(context)
    assert len(inputs)==1


@pytest.mark.parametrize('change',['profile','registry','credential','disable','source','expiry'])
def test_batch_proposal_rechecks_revocation_and_original_deadline(tmp_path,monkeypatch,change):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    clock=frozen_clock(monkeypatch,now)
    child=camera_child(wrapped,now);output_for_child(out,child)
    batch=store_shaped_batch(p,[sensor_child(now),child]);context=p(batch,now=now)
    if change=='profile':
        import eva.providers.home_assistant_procedure as module
        client=module.httpx.Client;response=client().stream('GET','/health')
        response.iter_bytes=lambda:iter((json.dumps({'profile_revision':'d'*64}).encode(),))
        monkeypatch.setattr(client,'stream',lambda *a,**kw:response)
    if change=='registry':p.discovery=SimpleNamespace(discover=lambda:replace(discover_cameras(*inventory()),revision='d'*64))
    if change=='credential':p.settings.credential='changed-account'
    if change=='disable':
        cfg=json.loads(path.read_text());cfg['enabled']=False;path.write_text(json.dumps(cfg))
    if change=='source':(tmp_path/'extension.py').write_text('changed')
    if change=='expiry':clock[0]=datetime.fromisoformat(child['occurred_at'])+timedelta(seconds=30)
    with pytest.raises(ValueError):p.validate_current(context)
    with pytest.raises(ValueError):p.validate_expression(batch,context['binding_sha256'])
    assert len(inputs)==1


def test_old_child_in_newer_batch_is_not_rejuvenated(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    old=camera_child(wrapped,now,age=31)
    assert p(store_shaped_batch(p,[old,sensor_child(now,age=0)]),now=now) is None
    assert not inputs


def test_batch_output_cannot_extend_original_camera_deadline(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    child=camera_child(wrapped,now,age=5);output_for_child(out,child)
    out['expires_at_epoch']=now.timestamp()+30
    with pytest.raises(ValueError):p(store_shaped_batch(p,[child]),now=now)


def test_large_valid_parent_does_not_duplicate_batch_in_model_context(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    child=camera_child(wrapped,now);output_for_child(out,child)
    members=[sensor_child(now,identifier='sensor'+str(i),description='x'*400) for i in range(7)]+[child]
    batch=store_shaped_batch(p,members)
    assert len(canonical_json_bytes(batch.payload['observation']))>4096
    context=p(batch,now=now)
    assert len(canonical_json_bytes(context))<=4096
    assert context['observation']==child and len(inputs)==1


def test_oversized_optional_context_preserves_ordinary_assessment_without_error(tmp_path,monkeypatch):
    p,wrapped,now,inputs,out,path=configured(tmp_path,monkeypatch)
    frozen_clock(monkeypatch,now)
    child=camera_child(wrapped,now);output_for_child(out,child)
    child['payload']['camera_observation']['scene']={'description':'s'*1500,'model':'fixture',
        'model_revision':'fixture','analyzed_at':now.isoformat()}
    out['text']='€'*600  # Valid character bound, larger UTF-8 model context.
    assert p(store_shaped_batch(p,[child]),now=now) is None
    assert len(inputs)==1


def test_provider_retains_original_host_context_without_publishing_it_to_model(tmp_path, monkeypatch):
    from eva.application.household_procedure import HouseholdProcedureContext, HouseholdProcedureModel
    from eva.application.free_will import FreeWillDecision
    p, event, now, inputs, output, path = configured(tmp_path, monkeypatch)
    context = p(event, now=now)
    assert isinstance(context, HouseholdProcedureContext)
    lineage = context.lineage.payload()
    assert lineage['context'] == dict(context)
    assert canonical_sha256(lineage['source_binding']) == context['source_binding_sha256']
    assert lineage['configuration']['config'] == json.loads(path.read_text())
    assert 'host-only' not in str(lineage) and 'face-secret' not in str(lineage)
    assert 'configuration' not in context and 'source_binding' not in context
    seen = []
    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            seen.append(kwargs['household_proposal_context'])
            assert 'configuration' not in kwargs['household_proposal_context']
            return FreeWillDecision('respond', 'Ordinary household choice', 1, 'isolated-model', 'Welkom')
    # Reuse the already-made result; never execute the isolated extension twice.
    class Prepared:
        def __call__(self, *args, **kwargs): return context
        def validate_current(self, value): return p.validate_current(value)
    result = HouseholdProcedureModel(Model(), Prepared(), authorize=lambda: None,
        clock=lambda: now).evaluate_sensory_event(event)
    assert result.household_procedure_lineage is context.lineage
    assert result.source_context_sha256 == canonical_sha256(lineage['context'])
    assert len(inputs) == 1 and len(seen) == 1


def test_versioned_source_uses_stable_artifact_identity_and_invalidates_previous_expression(tmp_path,monkeypatch):
    from eva.adapters.postgres.household_provisions import procedure_provision_id
    p,event,now,inputs,out,path=configured(tmp_path,monkeypatch)
    old_context=p(event,now=now); original=json.loads(path.read_text())
    stable=procedure_provision_id(original)
    changed={**original,'contract':'EvaHouseholdProcedureBinding/v2','provision_id':stable}
    path.write_text(json.dumps(changed))
    assert procedure_provision_id(changed)==stable
    with pytest.raises(ValueError,match='changed'):
        p.validate_expression(event,old_context['binding_sha256'])
    current=p(event,now=now)
    assert current.lineage.payload()['configuration']['config']==changed
    assert current['binding_sha256'] != old_context['binding_sha256']


def test_invalid_versioned_identity_stops_before_source_execution(tmp_path,monkeypatch):
    p,event,now,inputs,out,path=configured(tmp_path,monkeypatch)
    config=json.loads(path.read_text())
    config.update(contract='EvaHouseholdProcedureBinding/v2',provision_id='unbound-id')
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='identity differs'):
        p(event,now=now)
    assert inputs==[]
