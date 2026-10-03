import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import compare_followup as followup


def budget():
    return SimpleNamespace(calls=[],inflight=0,input=0,output=0,responses=0,
        limits={'maximum_total_input_tokens':600000,'maximum_total_output_tokens':48000})


def execute(results,b=None):
    b=b or budget();called=[];saved=[]
    async def run(case,arm,repeat,*args):
        called.append((repeat,case,arm))
        return deepcopy(results[len(called)-1] if len(called)<=len(results) else {'status':'completed'})
    report={'runs':[]}
    asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,work=None,contact=None,
        budget=b,limits={},run=run,save=lambda r:saved.append(deepcopy(r)),report=report))
    return called,saved,report


@pytest.mark.parametrize('reason',list(followup.LOCAL_REASONS))
def test_each_local_limit_is_a_failed_outcome_not_missing_other_arms(reason):
    called,saved,result=execute([{'status':'failed','error':'PerRunEnvelopeError','detail':reason}])
    assert called==list(followup.ORDER)
    assert saved[0]['runs'][0]['status']=='failed'
    assert saved[0]['runs'][0]['followupDisposition']['outcome']=='local_envelope_exhausted'
    assert result['status']=='completed_schedule_awaiting_independent_grading'
    assert len(result['runs'])==8


@pytest.mark.parametrize('error,detail',[
    ('BoundaryViolation','private content in shared delivery'),
    ('ValueError','frozen aggregate research envelope reached'),
    ('ValueError','isolated predictor execution failed'),
    ('RuntimeError','unknown provider failure')])
def test_hard_global_and_unexpected_errors_stop_after_persisting(error,detail):
    called,saved,result=execute([{'status':'failed','error':error,'detail':detail}])
    assert len(called)==1 and saved[-1]['runs'][0]['detail']==detail
    assert result['status']=='stopped_no_promotion'


def test_pending_or_unknown_usage_overrules_local_result():
    b=budget();b.inflight=1
    called,_,_=execute([{'status':'failed','error':'PerRunEnvelopeError','detail':'frozen response envelope reached'}],b)
    assert len(called)==1
    b=budget()
    b.input=600001
    called,_,_=execute([{'status':'completed'}],b)
    assert len(called)==1


@pytest.mark.parametrize("social_episode",[11,12])
def test_real_frozen_runner_awaits_and_counts_social_on_limit(monkeypatch,social_episode):
    original_root=HERE.parent;sys.path.insert(0,str(original_root))
    spec=importlib.util.spec_from_file_location('frozen_compare_for_offline_test',original_root/'compare.py')
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    monkeypatch.setattr(original,'configured_procedure_executor',lambda:object())
    limits={'maximum_model_responses_per_task_run':12,'maximum_tool_operations_per_task_run':24,
            'maximum_total_input_tokens':600000,'maximum_total_output_tokens':48000}
    b=original.Budget([],limits);social_finished=[]
    async def fake_ask(profile,adapter,budget,prompt,**kwargs):
        budget.admit()
        if kwargs['scope'].endswith(':social'):
            await asyncio.sleep(0);social_finished.append(True)
        usage={'attested':True,'inputTokens':10,'outputTokens':2,'requests':1}
        budget.retain(usage)
        return SimpleNamespace(output='done'),usage
    monkeypatch.setattr(original,'ask',fake_ask)
    episodes=[{'prompt':'synthetic offline control check'} for _ in range(social_episode+1)]
    episodes[-1]['social']={'prompt':'hello'}
    case={'id':'offline','state':{'version':1,'tasks':{}},'episodes':episodes}
    result=asyncio.run(followup.task_runner(original)(case,'ordinary',0,{'ordinary_method':'public method'},None,None,None,b,limits))
    assert result['detail']=='frozen response envelope reached'
    assert social_finished==([True] if social_episode==11 else [])
    if social_episode==11:assert result['episodes'][-1]['socialText']=='done'
    assert b.responses==12 and b.input==120 and b.output==24 and b.inflight==0
    assert followup.disposition(result,b,0).outcome==followup.RunOutcome.LOCAL_ENVELOPE_EXHAUSTED


@pytest.mark.parametrize('problem',['unattested','social','forged_provider'])
def test_local_label_never_hides_unknown_usage_social_or_provider_error(problem):
    b=budget();called=[];saved=[]
    async def run(*args):
        called.append(True)
        result={'status':'failed','error':'PerRunEnvelopeError','detail':'frozen response envelope reached'}
        if problem=='unattested':b.calls.append({'attested':False,'inputTokens':None,'outputTokens':None})
        if problem=='social':result['socialError']='BoundaryViolation'
        if problem=='forged_provider':result['error']='ValueError'
        return result
    asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,work=None,contact=None,
        budget=b,limits={},run=run,save=lambda r:saved.append(deepcopy(r)),report={'runs':[]}))
    assert len(called)==1 and saved[-1]['status']=='stopped_no_promotion'


def test_independent_boundary_check_stops_before_next_arm_and_retains_result():
    b=budget();calls=[];saved=[]
    async def run(*args):calls.append(True);return {'status':'completed','evidence':'retained'}
    report=asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,work=None,contact=None,
        budget=b,limits={},run=run,save=lambda r:saved.append(deepcopy(r)),report={'runs':[]},grade_stop=lambda result:True))
    assert len(calls)==1 and saved[-1]['runs'][0]['evidence']=='retained'
    assert report['status']=='stopped_independent_hard_boundary_no_promotion'
