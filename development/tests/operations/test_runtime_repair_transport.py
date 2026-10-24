"""Native repair tool uses the existing authenticated evaluation host, with bound IDs."""
from contextlib import contextmanager
from types import SimpleNamespace
import threading
from urllib.error import HTTPError

import pytest

from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient
from eva.adapters.postgres.component_improvement_actions import REPAIR
from eva.adapters.connectors.delegated_completion import completed_tool_evidence
from eva.application.conversation import ConversationActionCall,ConversationConflict
from eva.operations.component_evaluation_service import EvaluationServer
from tests.application.test_scoped_component_evaluation_actions import adapter


@contextmanager
def host(repairs):
    server=EvaluationServer(('127.0.0.1',0),evaluator=None,token='synthetic-token',allowed_paths=(),runtime_repairs=repairs)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();thread.join();server.server_close()


def test_transport_authentication_and_disabled_route_preserve_exact_scope(tmp_path):
    calls=[];repairs=SimpleNamespace(request=lambda request:calls.append(request) or {'state':'waiting_for_builder','accepted':True,'installed':False})
    token=tmp_path/'token';token.write_text('synthetic-token')
    arguments={'incident_id':'self-repair:'+'a'*64,'task_id':'work:eva-initiative:'+'b'*64,
        'request_id':'c'*64,'scope_sha256':'d'*64}
    with host(repairs) as url:
        client=ComponentEvaluationClient(url,str(token),('application/example.py',),'e'*64,runtime_repair_enabled=True)
        assert client.runtime_repair('submit',**arguments)['installed'] is False
        assert calls==[{'action':'submit',**arguments}]
        token.write_text('wrong-token')
        with pytest.raises(HTTPError) as error:client.runtime_repair('submit',**arguments)
        assert error.value.code==401 and len(calls)==1
    with host(None) as url:
        token.write_text('synthetic-token')
        client=ComponentEvaluationClient(url,str(token),('application/example.py',),'e'*64,runtime_repair_enabled=True)
        with pytest.raises(HTTPError) as error:client.runtime_repair('submit',**arguments)
        assert error.value.code==404


def test_tool_binds_incident_and_task_to_native_guard_and_replay_dispatches_once():
    component,checks,_=adapter();calls=[]
    component.action_names=(REPAIR,)
    component.evaluator=SimpleNamespace(runtime_repair=lambda action,**args:calls.append((action,args)) or
        {'state':'waiting_for_builder','accepted':True,'installed':False})
    guard=SimpleNamespace(check=lambda source:None,_repair_incident_id='self-repair:'+'a'*64,repair_role=None)
    component.repair_guard=guard
    call=ConversationActionCall('repair-handoff',REPAIR,{'action':'submit'})
    receipt=component.execute(component.source,call,step_index=3)
    assert component.execute(component.source,call,step_index=3)==receipt
    assert len(calls)==1 and calls[0][1]['incident_id']==guard._repair_incident_id
    assert calls[0][1]['task_id']==component.spec.work_id
    assert completed_tool_evidence([receipt]) is True  # Phase completion only.
    guard.repair_role='original_behavior_oracle'
    with pytest.raises(ConversationConflict,match='only read'):
        component.execute(component.source,ConversationActionCall('oracle-submit',REPAIR,{'action':'submit'}),step_index=4)
    component.repair_guard=None
    with pytest.raises(ConversationConflict,match='native repair context'):
        component.execute(component.source,ConversationActionCall('owner-submit',REPAIR,{'action':'submit'}),step_index=4)
    assert len(calls)==1
