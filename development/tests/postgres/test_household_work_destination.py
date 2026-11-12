"""Explicit shared-household result routing preserves sealed task authority."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from tests.postgres.test_household_public_work import setup, start, SAT
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_delegated_work_store import _usage
from eva.bootstrap import native_work_delivery as delivery_module
from eva.adapters.postgres.household_work_destination import read_destination

TARGET = "assist_satellite.office"

def route(x, work_id, key="route", target=TARGET):
    return x.service.execute("household_destination", x.turn, request_key=key,
        destination={"work_id": work_id, "satellite_id": target})

def finish(x, work_id):
    store=x.server.store
    from datetime import timedelta
    from eva.adapters.postgres.work_failure_notice import failure_notice_cutoff
    with store._engine.begin() as c:
        store._scope(c)
        failure_notice_cutoff(c,store,x.clock[0])
    x.clock[0] += timedelta(seconds=1)
    store._failure_report_clock=lambda:x.clock[0]
    claim=store.claim_next(worker_id="worker:destination", release=x.server.authority.profile.release, now=x.clock[0])
    call=store.start_call(claim, now=x.clock[0])
    store.record_failure(call, usage=_usage(), error_code="incomplete_work", error_fingerprint="d"*64,
        ambiguous=False, now=x.clock[0])

def test_explicit_destination_single_claim_and_sealed_origin(database, monkeypatch):
    x=setup(database,"explicit-route")
    wid=start(x)
    original=x.service._load(wid,x.service.origin(x.turn)).spec
    saved=route(x,wid)
    assert saved["destination"]["transport"]=="home_assistant_announce"
    assert saved["delivered"] is False
    assert saved["work"]["delivery_destination"]["delivery_status"]=="not_claimed"
    assert x.service.execute("list",x.turn,request_key="listed")["jobs"][0]["delivery_destination"]==saved["work"]["delivery_destination"]
    assert x.service._load(wid,x.service.origin(x.turn)).spec==original
    finish(x,wid)
    assert x.service.poll(x.turn) is None
    output=x.server.explicit_delivery_session(wid)
    turn=output.control_turn()
    claim=output.result_delivery(turn,"claim",work_id=wid)
    assert claim is not None
    assert x.service.execute("get",x.turn,request_key="status-claimed",work_id=wid)["work"]["delivery_destination"]["delivery_status"]=="claimed"
    assert output.result_delivery(turn,"claim",work_id=wid) is None
    with pytest.raises(ValueError,match="already claimed"):
        route(x,wid,"move-after-claim",SAT)
    output.result_delivery(turn,"ack",work_id=wid,result_sha256=claim["result_sha256"],disposition="offered")
    assert output.result_delivery(turn,"claim",work_id=wid) is None
    assert x.service.execute("get",x.turn,request_key="status-offered",work_id=wid)["work"]["delivery_destination"]["delivery_status"]=="offered"

def test_explicit_route_native_delivery_waits_for_streaming_target(database, monkeypatch):
    x=setup(database,"explicit-busy")
    wid=start(x);route(x,wid);finish(x,wid)
    args=x.args|{"satellite_id":TARGET,"device_id":"b"*32}
    target=x.server.session(**args,session_id=x.server.open_session(**args)["session_id"])
    sent=[]
    class Response:
        def __init__(self,data=None):self.data=data
        def json(self):return self.data
        def raise_for_status(self):pass
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,path):return Response({"entity_id":TARGET,"state":"idle"})
        def post(self,path,*,json):sent.append(json);return Response()
    monkeypatch.setattr(delivery_module.httpx,"Client",Client)
    worker=delivery_module.NativeWorkDelivery(x.server,SimpleNamespace(endpoint_url="http://ha.invalid",credential="fixture"))
    worker.run_once();assert sent==[]
    target.current("close")
    x.service.current("close")
    worker.run_once();worker.run_once()
    assert len(sent)==1 and sent[0]["entity_id"]==TARGET

def test_destination_revoked_and_invalid_target_denied(database,monkeypatch):
    x=setup(database,"explicit-revoked");wid=start(x)
    with pytest.raises(ValueError):route(x,wid,target="assist_satellite.unknown")
    route(x,wid);finish(x,wid)
    validate=x.server.authority.validate_device
    def revoked(satellite,device):
        if satellite==TARGET:raise ValueError("revoked")
        return validate(satellite,device)
    monkeypatch.setattr(x.server.authority,"validate_device",revoked)
    output=x.server.explicit_delivery_session(wid)
    with pytest.raises((ValueError,RuntimeError)):
        output.result_delivery(output.control_turn(),"claim",work_id=wid)

@pytest.mark.parametrize("kind",["research","digital"])
def test_atomic_start_destination_action_schema_and_replay(database,monkeypatch,kind):
    from eva.bootstrap.household_work import HouseholdWorkActions
    from eva.application.conversation import ConversationActionCall
    from tests.postgres.test_household_public_work import PUBLIC
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    if kind=="digital":
        from tests.postgres.test_household_digital_work import digital_setup
        x=digital_setup(database,"destination-atomic-digital")
        name="start_digital_work";args={"task":"Controleer de lampen"}
    else:
        x=setup(database,"destination-atomic-research")
        name="start_background_research";args={"research":PUBLIC}
    actions=HouseholdWorkActions(database,x.service)
    tools={item.name:item for item in actions.conversation_tools()}
    assert "set_household_work_destination" in tools and "set_household_work_destination" in actions.action_names
    assert "set_household_work_destination" not in {item.name for item in PostgresDelegatedWorkActions.conversation_tools(include_digital=True,include_research=True)}
    assert "destination_satellite_id" in tools[name].input_schema["properties"]
    call=ConversationActionCall("atomic",name,args|{"destination_satellite_id":TARGET})
    receipt=actions.execute(x.turn,call)
    assert receipt.output["status"]=="verified"
    work_id=receipt.output["operation_result"]["work"]["work_id"]
    assert receipt.output["operation_result"]["work"]["delivery_destination"]["satellite_id"]==TARGET
    assert actions.execute(x.turn,call)==receipt
    with database.begin() as c:
        x.server.store._scope(c)
        assert read_destination(c,x.server.store.scope,work_id)["satellite_id"]==TARGET


def test_atomic_destination_failure_rolls_back_admission(database,monkeypatch):
    from eva.adapters.postgres import household_work_destination as module
    from tests.postgres.test_household_public_work import PUBLIC
    x=setup(database,"destination-rollback")
    def fail(*args,**kwargs):raise ValueError("destination write unavailable")
    monkeypatch.setattr(module,"save_destination",fail)
    with pytest.raises(ValueError,match="unavailable"):
        x.service.execute("research",x.turn,request_key="atomic",research=PUBLIC,destination_satellite_id=TARGET)
    assert x.service.execute("list",x.turn,request_key="list")["jobs"]==[]


def test_resume_inherits_explicit_destination_without_origin_change(database):
    x=setup(database,"destination-resume");wid=start(x);route(x,wid)
    original=x.service._load(wid,x.service.origin(x.turn)).spec.origin
    x.service.execute("stop",x.turn,request_key="stop",work_id=wid)
    new=x.service.execute("resume",x.turn,request_key="resume",work_id=wid)["work"]
    assert new["delivery_destination"]["satellite_id"]==TARGET
    assert x.service._load(new["work_id"],original).spec.origin==original


def test_destination_change_during_idle_probe_uses_claimed_destination(database,monkeypatch):
    x=setup(database,"destination-race");wid=start(x);route(x,wid);finish(x,wid)
    # Keep the foreground lease alive for the explicit change; after the
    # first probe close it so the final destination is free for output.
    sent=[];probes=[]
    class Response:
        def __init__(self,data=None):self.data=data
        def json(self):return self.data
        def raise_for_status(self):pass
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,path):
            satellite=path.removeprefix("/api/states/");probes.append(satellite)
            if len(probes)==1:
                route(x,wid,"change-before-claim",SAT)
                x.service.current("close")
            return Response({"entity_id":satellite,"state":"idle"})
        def post(self,path,*,json):sent.append(json);return Response()
    monkeypatch.setattr(delivery_module.httpx,"Client",Client)
    worker=delivery_module.NativeWorkDelivery(x.server,SimpleNamespace(endpoint_url="http://ha.invalid",credential="fixture"))
    worker.run_once();worker.run_once()
    assert probes==[TARGET,SAT]
    assert len(sent)==1 and sent[0]["entity_id"]==SAT


def test_destination_action_replay_rechecks_current_selection_and_admission(database,monkeypatch):
    from eva.bootstrap.household_work import HouseholdWorkActions
    from eva.application.conversation import ConversationActionCall
    x=setup(database,"destination-replay");wid=start(x)
    actions=HouseholdWorkActions(database,x.service)
    call=ConversationActionCall("route","set_household_work_destination",{
        "destination":{"work_id":wid,"satellite_id":TARGET}})
    turn=x.service.turn(str(uuid4()),"Geef het antwoord op het andere apparaat")
    receipt=actions.execute(turn,call)
    assert actions.execute(turn,call)==receipt
    validate=x.server.authority.validate_device
    def revoked(satellite,device):
        if satellite==TARGET:raise ValueError("revoked")
        return validate(satellite,device)
    monkeypatch.setattr(x.server.authority,"validate_device",revoked)
    with pytest.raises(ValueError,match="revoked"):
        actions.execute(turn,call)
    monkeypatch.setattr(x.server.authority,"validate_device",validate)
    route(x,wid,"move-again",SAT)
    with pytest.raises(ValueError,match="superseded"):
        actions.execute(turn,call)

@pytest.mark.parametrize('failure_stage', ['probe', 'send'])
def test_explicit_delivery_probe_failure_defers_but_send_failure_is_ambiguous(database, monkeypatch, failure_stage):
    x=setup(database,'explicit-failure-'+failure_stage)
    wid=start(x);route(x,wid);finish(x,wid)
    x.service.current('close')
    probes=[];sent=[]
    class Response:
        def json(self):return {'entity_id':TARGET,'state':'idle'}
        def raise_for_status(self):pass
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,path):
            probes.append(path)
            if failure_stage=='probe' and len(probes)==2:raise TimeoutError()
            return Response()
        def post(self,path,*,json):
            sent.append(json)
            if failure_stage=='send':raise TimeoutError()
            return Response()
    monkeypatch.setattr(delivery_module.httpx,'Client',Client)
    worker=delivery_module.NativeWorkDelivery(x.server,SimpleNamespace(endpoint_url='http://ha.invalid',credential='fixture'))
    worker.run_once()
    service=x.server.explicit_delivery_session(wid)
    work=service._load(wid,service.origin(service.control_turn()))
    assert service.view(work)['delivery_destination']['delivery_status']==('deferred' if failure_stage=='probe' else 'ambiguous')
    worker.run_once()
    assert len(sent)==1
    assert service.view(work)['delivery_destination']['delivery_status']==('offered' if failure_stage=='probe' else 'ambiguous')

@pytest.mark.parametrize('kind',['research','digital'])
def test_start_receipt_cannot_repeat_superseded_destination(database,kind):
    from eva.bootstrap.household_work import HouseholdWorkActions
    from eva.application.conversation import ConversationActionCall
    from tests.postgres.test_household_public_work import PUBLIC
    if kind=='digital':
        from tests.postgres.test_household_digital_work import digital_setup
        x=digital_setup(database,'start-destination-replay-digital')
        name='start_digital_work';args={'task':'Controleer de lampen'}
    else:
        x=setup(database,'start-destination-replay-research')
        name='start_background_research';args={'research':PUBLIC}
    actions=HouseholdWorkActions(database,x.service)
    call=ConversationActionCall('start-route',name,args|{'destination_satellite_id':TARGET})
    receipt=actions.execute(x.turn,call)
    wid=receipt.output['operation_result']['work']['work_id']
    route(x,wid,'move',SAT)
    with pytest.raises(ValueError,match='superseded'):
        actions.execute(x.turn,call)
