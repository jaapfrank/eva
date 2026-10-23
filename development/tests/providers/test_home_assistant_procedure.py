from datetime import UTC, datetime
from types import SimpleNamespace
from dataclasses import replace
import hashlib,json
import pytest
from eva.providers.home_assistant_procedure import HomeAssistantProcedure
from tests.connectors.test_camera_observer import binding, inventory, event
from eva.adapters.connectors.camera_observer import discover_cameras


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
