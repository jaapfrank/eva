"""Ordered HA updates, read fences and invalidation on the existing socket."""
import json
import queue
import threading
import time
from contextlib import contextmanager
import pytest
from eva.adapters.connectors.home_assistant_context import HomeAssistantDeviceContext

class Socket:
    def __init__(self):self.frames=queue.Queue();self.pongs=True;self.results={}
    def send(self,raw):
        request=json.loads(raw);number=request["id"];kind=request["type"]
        if kind=="ping":
            if self.pongs:self.push({"id":number,"type":"pong"})
            return
        result={"id":number,"type":"result","success":True,"result":None}
        if kind=="config/area_registry/list":result["result"]=[{"area_id":"yard","name":"Tuin"}]
        elif kind=="config/device_registry/list":result["result"]=[]
        elif kind=="config/entity_registry/list":result["result"]=[{"entity_id":"light.yard","area_id":"yard","device_id":None,"disabled_by":None,"created_at":1234.5}]
        elif kind=="auth/current_user":result["result"]={"id":"owner","is_admin":True,"created_at":1234.5}
        if kind in self.results:result["result"]=self.results[kind]
        self.push(result)
        if kind=="subscribe_entities":self.delta({"a":{"light.yard":{"s":"off","a":{"friendly_name":"Tuin","entity_id":["light.a"]},"lc":100.5}}})
    def push(self,frame):self.frames.put(json.dumps(frame))
    def delta(self,delta):self.push({"id":2,"type":"event","event":delta})
    def recv(self,timeout):
        try:return self.frames.get(timeout=timeout)
        except queue.Empty:raise TimeoutError

@contextmanager
def connected(context=None):
    context=context or HomeAssistantDeviceContext(scope_sha256="scope");socket=Socket();stop=threading.Event();errors=[]
    def run():
        try:context.listen(socket,stop,lambda raw:None,version="2026.9.2")
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=run);thread.start()
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        with context._lock:
            if context._ready():break
        time.sleep(.002)
    try:yield context,socket,errors
    finally:stop.set();thread.join(2);assert not thread.is_alive()


def test_initial_external_floats_and_queued_deltas_are_fenced_before_read():
    with connected() as (context,socket,errors):
        first=context.snapshot();assert len(first["states"])==1
        assert first["inventory"]["areas"][0]["name"]=="Tuin"
        socket.delta({"c":{"light.yard":{"+":{"s":"on","lu":101.25}}}})
        second=context.snapshot();assert second["states"][0]["state"]=="on"
        assert second["binding_sha256"]==first["binding_sha256"]
        socket.delta({"c":{"light.yard":{"+":{"a":{"entity_id":["light.b"]}},"-":{"a":["friendly_name"]}}}})
        third=context.snapshot();assert third["binding_sha256"]!=first["binding_sha256"]
        assert "friendly_name" not in third["states"][0]["attributes"]
    assert not errors
    with pytest.raises(RuntimeError):context.snapshot()


def test_identical_registry_refresh_keeps_binding_but_reconnect_changes_it():
    context=HomeAssistantDeviceContext(scope_sha256="scope")
    with connected(context) as (_,socket,errors):
        before=context.snapshot()["binding_sha256"]
        socket.push({"id":3,"type":"event","event":{"event_type":"area_registry_updated"}})
        # A barrier processed after the registry event may find a refresh in progress.
        deadline=time.monotonic()+2
        while True:
            try:after=context.snapshot()["binding_sha256"];break
            except RuntimeError:
                assert time.monotonic()<deadline;time.sleep(.002)
        assert after==before
    with connected(context):assert context.snapshot()["binding_sha256"]!=after
    with connected() as (other,_,_):assert other.snapshot()["binding_sha256"]!=after
    assert not errors


def test_missing_pong_never_returns_stale_state():
    with connected() as (context,socket,errors):
        context.snapshot();socket.pongs=False
        with pytest.raises(TimeoutError):context.snapshot(timeout=.02)
    assert not errors


@pytest.mark.parametrize("kind,result",[
    ("config/area_registry/list",[{"area_id":"yard","name":"Achtertuin"}]),
    ("config/device_registry/list",[{"id":"lamp-device","area_id":"yard"}]),
    ("config/entity_registry/list",[{"entity_id":"light.yard","area_id":"other","disabled_by":None}]),
    ("auth/current_user",{"id":"owner","is_admin":False,"created_at":1234.5}),
])
def test_changed_registry_content_still_invalidates_after_refresh(kind,result):
    with connected() as (context,socket,errors):
        before=context.binding_snapshot()["binding_sha256"]
        socket.results[kind]=result
        socket.push({"id":3,"type":"event","event":{"event_type":"area_registry_updated"}})
        deadline=time.monotonic()+2
        while True:
            try:after=context.binding_snapshot()["binding_sha256"];break
            except RuntimeError:
                assert time.monotonic()<deadline;time.sleep(.002)
        assert after!=before
    assert not errors


def test_overlapping_refresh_cannot_expose_the_previous_binding():
    with connected() as (context,socket,errors):
        before=context.binding_snapshot()["binding_sha256"]
        held=[];send=socket.send
        def hold(raw):
            if json.loads(raw)["type"]!="ping":held.append(raw)
            else:send(raw)
        with context._lock:
            context._sender=hold
            context._refresh();context._refresh()
        with pytest.raises(RuntimeError):context.binding_snapshot()
        first=list(held);held.clear()
        for raw in first:send(raw)
        deadline=time.monotonic()+2
        while len(held)<4:
            assert time.monotonic()<deadline;time.sleep(.002)
        with pytest.raises(RuntimeError):context.binding_snapshot()
        for raw in held:send(raw)
        while True:
            try:after=context.binding_snapshot()["binding_sha256"];break
            except RuntimeError:
                assert time.monotonic()<deadline;time.sleep(.002)
        assert after==before
    assert not errors


def test_entity_bookkeeping_timestamp_does_not_discard_an_unchanged_selection():
    with connected() as (context,socket,errors):
        before=context.snapshot()
        rows=[{"entity_id":"light.yard","area_id":"yard","device_id":None,
               "disabled_by":None,"created_at":1234.5,"modified_at":5678.5}]
        socket.results["config/entity_registry/list"]=rows
        socket.push({"id":5,"type":"event","event":{"event_type":"entity_registry_updated"}})
        deadline=time.monotonic()+2
        while True:
            try:after=context.binding_snapshot();break
            except RuntimeError:
                assert time.monotonic()<deadline;time.sleep(.002)
        assert after["binding_sha256"]==before["binding_sha256"]
        # Filtering belongs only to the comparison; retain the actual source.
        with context._lock:
            assert context._registries["entities"][0]["modified_at"]==5678.5
    assert not errors


def test_unknown_entity_delta_disconnects_and_invalidates_snapshot():
    with connected() as (context,socket,errors):
        context.snapshot();socket.delta({"c":{"light.unknown":{"+":{"s":"on"}}}})
        deadline=time.monotonic()+2
        while not errors and time.monotonic()<deadline:time.sleep(.002)
        with pytest.raises(RuntimeError):context.snapshot()
    assert len(errors)==1 and isinstance(errors[0],ValueError)


def test_added_renamed_and_removed_entity_never_survives_as_old_context():
    with connected() as (context,socket,errors):
        before=context.snapshot()["binding_sha256"]
        socket.delta({"a":{"switch.new":{"s":"off","a":{"friendly_name":"Nieuw"},"lc":102.5}}})
        assert context.catalog("switch")[0]["entity_id"]=="switch.new"
        socket.delta({"c":{"light.yard":{"+":{"a":{"friendly_name":"Achtertuin"}}}}})
        changed=context.snapshot()
        assert changed["binding_sha256"]!=before
        assert changed["states"][0]["attributes"]["friendly_name"]=="Achtertuin"
        socket.delta({"r":["switch.new"]})
        assert context.catalog("switch")==[]
    assert not errors


def test_rejected_registry_refresh_cannot_keep_an_authorized_old_snapshot():
    with connected() as (context,socket,errors):
        context.snapshot()
        socket.push({"id":3,"type":"result","success":False,"error":{"code":"unauthorized"}})
        deadline=time.monotonic()+2
        while not errors and time.monotonic()<deadline:time.sleep(.002)
        with pytest.raises(RuntimeError):context.snapshot()
    assert len(errors)==1 and isinstance(errors[0],RuntimeError)


def test_binding_and_catalog_reads_keep_fences_without_building_unused_inventory(monkeypatch):
    with connected() as (context,socket,errors):
        expected=context.snapshot()
        monkeypatch.setattr(context,"_inventory",lambda:pytest.fail("unused full inventory"))
        binding=context.binding_snapshot()
        assert binding["binding_sha256"]==expected["binding_sha256"] and binding["complete"]
        binding["binding_sha256"]="caller modified"
        catalog=context.catalog("light");assert catalog==expected["states"]
        catalog[0]["attributes"]["friendly_name"]="caller modified"
        assert context.catalog("light")[0]["attributes"]["friendly_name"]=="Tuin"
        socket.delta({"c":{"light.yard":{"+":{"a":{"friendly_name":"Achtertuin"}}}}})
        assert context.binding_snapshot()["binding_sha256"]!=expected["binding_sha256"]
        socket.pongs=False
        with pytest.raises(TimeoutError):context.binding_snapshot(timeout=.02)
    assert not errors
    with pytest.raises(RuntimeError):context.binding_snapshot()
