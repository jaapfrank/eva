"""Actual sandbox protocol with controlled durable service receipts; no provider traffic."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from eva.adapters.postgres.tool_program_actions import ToolProgramActions, NAME
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationConflict
from eva.contracts.canonical_json import canonical_sha256

class Timeline:
    def __init__(self): self.rows = {}
    def events(self, thread): return self.rows.get(thread, [])
    def create_thread(self, **kw):
        assert not self.events(kw["thread_id"])
        return self.transition(expected_revision=0, **kw)
    def transition(self, expected_revision, **kw):
        rows = self.rows.setdefault(kw["thread_id"], [])
        assert len(rows) == expected_revision
        rows.append(SimpleNamespace(sequence=len(rows)+1, event_type=kw["event_type"], payload={"data":kw["data"]}))

class Client:
    image_id = "sha256:" + "a"*64
    def __init__(self, directory): self.directory, self.rounds = directory, 0
    def execute(self, source, input_data):
        self.rounds += 1
        path = self.directory / "program.py"; path.write_text(source)
        raw = json.dumps(input_data, ensure_ascii=False, sort_keys=True, separators=(",",":"),allow_nan=False).encode()
        r = subprocess.run([sys.executable,"-I","-B",str(path)],input=raw,capture_output=True,timeout=5)
        return {"contract":"EvaIsolatedProcedureExecution/v1","image_id":self.image_id,
            "source_sha256":hashlib.sha256(source.encode()).hexdigest(),
            "input_sha256":hashlib.sha256(raw).hexdigest(),
            "result":{"exit_code":r.returncode,"timed_out":False,"cancelled":False,
                "stdout_truncated":False,"stderr_truncated":False,
                "stdout":r.stdout.decode(),"stderr":r.stderr.decode()}}

class Actions:
    def __init__(self): self.saved, self.effects, self.receipts = {}, [], []
    def pending_step(self, turn, *, step_index):
        r=self.saved.get(step_index)
        if r:self.receipts.append(r)
        return r
    def execute_step(self, turn, call, *, step_index):
        assert step_index not in self.saved
        self.effects.append((call.name,call.arguments,step_index))
        r=ConversationActionReceipt(call,{"status":"verified","value":call.arguments.get("value",7)})
        self.saved[step_index]=r;self.receipts.append(r)
        return r

@pytest.fixture
def fixture(tmp_path):
    source=SimpleNamespace(tenant_id="tenant",installation_id="installation")
    spec=SimpleNamespace(execution_id="work:original",source=SimpleNamespace(model_dump=lambda **k:{"message":"original"}),source_ingress_sha256="b"*64,grant=SimpleNamespace(maximum_tool_calls=31))
    timeline,client,actions=Timeline(),Client(tmp_path),Actions()
    state={"stopped":False,"checks":0,"dispatches":0}
    def authorize(turn):
        assert turn is source;state["checks"]+=1
        if state["stopped"]:raise ConversationConflict("stopped")
    def dispatch(turn):authorize(turn);state["dispatches"]+=1
    def build():return ToolProgramActions(None,spec,source,authorize,dispatch,lambda:None,
        actions=lambda:actions,allowed_names={"first_service","second_service"},client=client,timeline=timeline)
    return SimpleNamespace(source=source,spec=spec,timeline=timeline,client=client,actions=actions,state=state,build=build)

def call(source):return ConversationActionCall("outer",NAME,{"source":source,"input_json":"{\"value\":9}"})

PROGRAM="""a=eva_tools.call('first_service',value=input_data['value'])
b=eva_tools.call('second_service',value=a['value']+1)
print(b['value'])"""

def test_two_services_and_replay_never_repeat_effects(fixture):
    f=fixture;adapter=f.build();c=call(PROGRAM)
    r=adapter.execute(f.source,c,step_index=3)
    assert r.output["program_result"]["output"].strip()=="10"
    assert [x[0] for x in f.actions.effects]==["first_service","second_service"]
    assert f.client.rounds==3 and f.state["dispatches"]==3
    assert min(x[2] for x in f.actions.effects)>2**32
    assert adapter._substep(3,0)!=adapter._substep(4,0)
    assert f.build().pending(f.source,step_index=3)==r
    assert f.build().execute(f.source,c,step_index=3)==r
    assert len(f.actions.effects)==2 and f.client.rounds==3

@pytest.mark.parametrize("mutation",["source","input_json","call_id"])
def test_program_binding_cannot_drift(fixture,mutation):
    f=fixture;c=call(PROGRAM);f.build().execute(f.source,c,step_index=2)
    args=dict(c.arguments)
    if mutation in args:args[mutation]+=" "
    changed=ConversationActionCall("changed" if mutation=="call_id" else c.call_id,c.name,args)
    with pytest.raises(ConversationConflict,match="replay differs"):
        f.build().execute(f.source,changed,step_index=2)
    assert len(f.actions.effects)==2

def test_stop_after_first_service_prevents_second(fixture):
    f=fixture;original=f.actions.execute_step
    def effect(*args,**kwargs):
        r=original(*args,**kwargs);f.state["stopped"]=True;return r
    f.actions.execute_step=effect
    with pytest.raises(ConversationConflict,match="stopped"):
        f.build().execute(f.source,call(PROGRAM))
    assert len(f.actions.effects)==1 and f.client.rounds==1

def test_crash_after_effect_before_program_receipt_recovers_same_effect(fixture):
    f=fixture;original=f.actions.execute_step
    def effect(*args,**kwargs):
        r=original(*args,**kwargs)
        if len(f.actions.effects)==1:raise RuntimeError("host crash after service committed")
        return r
    f.actions.execute_step=effect
    with pytest.raises(RuntimeError,match="host crash"):
        f.build().execute(f.source,call(PROGRAM))
    f.actions.execute_step=original
    r=f.build().execute(f.source,call(PROGRAM))
    assert r.output["program_result"]["output"].strip()=="10"
    assert len(f.actions.effects)==2

def test_uncertain_effect_is_not_bypassed_after_resume(fixture):
    f=fixture;attempts=[]
    def uncertain(turn,call,*,step_index):
        attempts.append(step_index)
        r=ConversationActionReceipt(call,{"status":"uncertain"})
        f.actions.saved[step_index]=r
        return r
    f.actions.execute_step=uncertain
    with pytest.raises(ConversationConflict,match="did not confirm"):
        f.build().execute(f.source,call(PROGRAM))
    with pytest.raises(ConversationConflict,match="did not confirm"):
        f.build().execute(f.source,call(PROGRAM))
    assert len(attempts)==1

def test_unknown_action_does_not_dispatch(fixture):
    f=fixture
    with pytest.raises(ConversationConflict,match="unadmitted"):
        f.build().execute(f.source,call("eva_tools.call('install_whatever')"))
    assert not f.actions.effects

def test_program_stdout_does_not_prove_service_completion(fixture):
    from eva.adapters.connectors.delegated_completion import completed_tool_evidence
    f=fixture;r=f.build().execute(f.source,call("print('verified')"))
    assert r.output["status"]=="verified"
    assert completed_tool_evidence([r]) is False


def test_float_json_data_uses_executor_json_binding_without_new_contract_restriction(fixture):
    f=fixture;c=ConversationActionCall("float",NAME,{"source":"print(input_data['value'] * 2)","input_json":'{"value":1.5}'})
    r=f.build().execute(f.source,c)
    assert r.output["program_result"]["output"].strip()=="3.0"
    assert not f.actions.effects


def test_oversize_local_history_is_actionable_not_an_ambiguous_task_failure(fixture):
    from eva.adapters.connectors.delegated_completion import completed_tool_evidence
    f=fixture;original=f.actions.execute_step
    def large(*args,**kwargs):
        r=original(*args,**kwargs)
        r=ConversationActionReceipt(r.call,{"status":"verified","value":"x"*33000})
        from eva.adapters.postgres.connector_results import ConnectorResults, _encoded, retained_action_body
        body=retained_action_body(r);raw=_encoded(body)
        capture=object.__new__(ConnectorResults);capture._bytes=lambda _: (raw,hashlib.sha256(raw).hexdigest())
        manifest=capture._manifest("a"*64,body);manifest.update(preview="",preview_omitted=True)
        projection={"contract":"EvaRetainedActionBodyModelView/v1", "source":{
            "thread_id":"original","call_id":r.call.call_id,"step_index":kwargs["step_index"],
            "observation_key":"action_body","receipt_sha256":canonical_sha256(r.output),
            "proof_sha256":canonical_sha256(None),"source_ingress_sha256":"b"*64},"manifest":manifest}
        r=ConversationActionReceipt(r.call,r.output,model_output={"ancestor_model_projection":projection})
        f.actions.saved[kwargs["step_index"]]=r
        return r
    f.actions.execute_step=large
    c=call(PROGRAM);r=f.build().execute(f.source,c)
    assert r.output["program_result"]["state"]=="input_not_transferable"
    assert len(f.actions.effects)==1 and f.client.rounds==1
    readers=r.output["program_result"]["retained_readers"]
    assert len(readers)==1 and readers[0]["reader"]["action"]=="use_connector_tool"
    assert "preview" not in readers[0] and len(json.dumps(readers))<8192
    assert completed_tool_evidence([r]) is False
    assert f.build().pending(f.source)==r
    assert f.client.rounds==1
    # A later independently verified result may finish the same task; the
    # refused transfer itself supplies no success evidence.
    later=ConversationActionReceipt(ConversationActionCall("later","service",{}),{"status":"verified"})
    assert completed_tool_evidence([r,later]) is True


@pytest.mark.parametrize("source,code", [
    ("a=eva_tools.call('first_service'); print(a['missing'])", "user_exception"),
    ("a=eva_tools.call('first_service'); print('x'*17000)", "stdout_bound"),
])
def test_expected_local_failure_is_retained_context_not_task_success_or_retry(fixture, source, code):
    from eva.adapters.connectors.delegated_completion import completed_tool_evidence
    f=fixture; c=call(source); r=f.build().execute(f.source,c)
    assert r.output["program_result"]["state"]=="execution_failed"
    assert r.output["program_result"]["error"]["code"]==code
    assert r.output["program_result"]["subcalls"]==1
    assert len(f.actions.effects)==1 and f.client.rounds==2
    assert completed_tool_evidence([*f.actions.receipts,r]) is False
    assert f.build().execute(f.source,c)==r
    assert len(f.actions.effects)==1 and f.client.rounds==2
    later=ConversationActionReceipt(ConversationActionCall("later","service",{}),{"status":"verified"})
    assert completed_tool_evidence([*f.actions.receipts,r,later]) is True
